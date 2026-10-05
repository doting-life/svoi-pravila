"""Telegram contacts flow, dialog intercept, and command menu."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from aiogram import Bot
from aiogram.methods import EditMessageReplyMarkup, SendMessage, SetMyCommands
from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, Message, Update, User
from tests.factories import make_settings
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.dialog import FakeDialogState
from tests.fakes.generation import FakeTextGenerator
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.rate_limit import FakePseudonymizer
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.usage_sink import RecordingUsageEventSink

from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.handlers.contacts import (
    AwaitingDialogText,
    _parse_contact_id,
    _parse_relationship,
    contacts_command,
    dialog_text,
)
from svoi_pravila.adapters.channels.telegram.keyboards import (
    _require_callback_bytes,
    contacts_keyboard,
    relationship_keyboard,
)
from svoi_pravila.adapters.channels.telegram.lifecycle import TelegramLifecycle
from svoi_pravila.adapters.channels.telegram.localization import (
    load_ru_strings,
    relationship_label,
)
from svoi_pravila.application.errors import AccessNotGranted
from svoi_pravila.application.ports.dialog_state import DIALOG_PSEUDONYM_PURPOSE, DialogRecord
from svoi_pravila.application.use_cases.create_contact import (
    CreateContact,
    CreateContactCommand,
    CreateContactResult,
)
from svoi_pravila.application.use_cases.get_user_by_telegram_id import (
    GetUserByTelegramIdQuery,
    GetUserByTelegramIdResult,
)
from svoi_pravila.application.use_cases.list_contacts import ListContactsCommand
from svoi_pravila.application.use_cases.rename_contact import (
    RenameContact,
    RenameContactCommand,
    RenameContactResult,
)
from svoi_pravila.application.use_cases.revoke_all_consents import RevokeAllConsentsCommand
from svoi_pravila.config import Environment, Settings, TelegramUpdatesMode
from svoi_pravila.domain.access import AccessStatus
from svoi_pravila.domain.contact import MAX_CONTACTS_PER_USER, Contact
from svoi_pravila.domain.enums import ConsentKind, RelationshipKind
from svoi_pravila.domain.ids import ContactId, PairId, TelegramUserId, UserId
from svoi_pravila.domain.text import ContactLabel

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_LABEL_SENTINEL = "SENTINEL_CONTACT_LABEL_0009"


def _settings() -> Settings:
    return make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )


def _text_update(update_id: int, user_id: int, text: str) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=_NOW,
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            text=text,
        ),
    )


def _callback(update_id: int, user_id: int, data: str) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(update_id),
            from_user=User(id=user_id, is_bot=False, first_name="A"),
            chat_instance="x",
            data=data,
            message=Message(
                message_id=1,
                date=_NOW,
                chat=Chat(id=user_id, type="private"),
                from_user=User(id=user_id, is_bot=False, first_name="A"),
                text="p",
            ),
        ),
    )


async def _onboard(
    bot: Bot,
    lifecycle: TelegramLifecycle,
    user_id: int,
    catalog: FakeConsentCatalog,
) -> None:
    start = 10_000 + user_id * 10
    await lifecycle.dispatcher.feed_update(bot, _text_update(start, user_id, "/start"))
    await lifecycle.dispatcher.feed_update(bot, _callback(start + 1, user_id, "age:y"))
    pd = catalog.current_requirement().for_kind(ConsentKind.PERSONAL_DATA).version
    sc = catalog.current_requirement().for_kind(ConsentKind.SPECIAL_CATEGORY).version
    await lifecycle.dispatcher.feed_update(
        bot, _callback(start + 2, user_id, f"cg:personal_data:{pd}:y")
    )
    await lifecycle.dispatcher.feed_update(
        bot, _callback(start + 3, user_id, f"cg:special_category:{sc}:y")
    )


def _sent_texts(session: FakeTelegramSession) -> list[str]:
    return [str(req.text) for req in session.requests if isinstance(req, SendMessage)]


def _dialog_key(user_id: int) -> str:
    return FakePseudonymizer().pseudonymize(DIALOG_PSEUDONYM_PURPOSE, str(user_id))


@pytest.mark.unit
async def test_contacts_list_add_rename_active_and_decode_untouched() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    sink = RecordingUsageEventSink()
    generator = FakeTextGenerator()
    dialog = FakeDialogState()
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            generator=generator,
            sink=sink,
            dialog=dialog,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 501, catalog)
    await lifecycle.dispatcher.feed_update(bot, _text_update(10, 501, "/contacts"))
    assert any(deps.strings.contacts_empty in text for text in _sent_texts(session))
    await lifecycle.dispatcher.feed_update(bot, _callback(11, 501, "ct:n"))
    assert any(isinstance(req, EditMessageReplyMarkup) for req in session.requests)
    await lifecycle.dispatcher.feed_update(bot, _callback(12, 501, "ct:rel:friend"))
    assert deps.strings.contacts_label_prompt in _sent_texts(session)
    stored = await dialog.get(_dialog_key(501))
    assert stored is not None
    assert stored.step == "awaiting_label"
    await lifecycle.dispatcher.feed_update(bot, _text_update(13, 501, "Sam"))
    assert generator.decode_stream_calls == []
    assert sink.events == []
    assert await dialog.get(_dialog_key(501)) is None
    assert "Sam" in _sent_texts(session)[-1]
    assert deps.strings.contacts_active_mark in _sent_texts(session)[-1]
    owner = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(501)))
    ).user
    assert owner is not None
    listed_result = await deps.list_contacts.execute(ListContactsCommand(owner.id))
    contact_id = listed_result.contacts[0].id
    await lifecycle.dispatcher.feed_update(bot, _callback(14, 501, f"ct:r:{contact_id}"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(15, 501, "Pat"))
    assert "Pat" in _sent_texts(session)[-1]
    await lifecycle.dispatcher.feed_update(bot, _callback(16, 501, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(17, 501, "ct:rel:work"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(18, 501, "Lin"))
    owner = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(501)))
    ).user
    assert owner is not None
    listed_result = await deps.list_contacts.execute(ListContactsCommand(owner.id))
    second_id = next(c.id for c in listed_result.contacts if c.label.value == "Lin")
    await lifecycle.dispatcher.feed_update(bot, _callback(19, 501, f"ct:a:{second_id}"))
    listed = _sent_texts(session)[-1]
    assert "Lin" in listed
    await lifecycle.dispatcher.feed_update(bot, _text_update(20, 501, "please decode this now"))
    assert generator.decode_stream_calls
    assert sink.events


@pytest.mark.unit
async def test_invalid_label_keeps_dialog() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    dialog = FakeDialogState()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog, generator=generator)
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 502, catalog)
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 502, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 502, "ct:rel:other"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, 502, ""))
    assert deps.strings.contacts_invalid_label in _sent_texts(session)
    assert await dialog.get(_dialog_key(502)) is not None
    await lifecycle.dispatcher.feed_update(bot, _text_update(4, 502, ""))
    assert await dialog.get(_dialog_key(502)) is not None
    assert generator.decode_stream_calls == []


@pytest.mark.unit
async def test_contact_limit_clears_dialog_then_decode() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    ids = FakeIdGenerator()
    dialog = FakeDialogState()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog, ids=ids, generator=generator)
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 530, catalog)
    user = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(530)))
    ).user
    assert user is not None
    create = CreateContact(uow, catalog, ids, deps.clock)
    for i in range(MAX_CONTACTS_PER_USER):
        await create.execute(
            CreateContactCommand(user.id, ContactLabel(f"n{i}"), RelationshipKind.OTHER)
        )
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 530, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 530, "ct:rel:family"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, 530, "overflow"))
    assert deps.strings.contacts_limit in _sent_texts(session)
    assert await dialog.get(_dialog_key(530)) is None
    await lifecycle.dispatcher.feed_update(bot, _text_update(4, 530, "please decode this now"))
    assert len(generator.decode_stream_calls) == 1


@pytest.mark.unit
async def test_cancel_and_any_command_clears_dialog() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    dialog = FakeDialogState()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 503, catalog)
    await dialog.set(
        _dialog_key(503),
        DialogRecord(step="awaiting_label", relationship=RelationshipKind.PARTNER),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(1, 503, "/help"))
    assert await dialog.get(_dialog_key(503)) is None
    await dialog.set(
        _dialog_key(503),
        DialogRecord(step="awaiting_rename", contact_id=ContactId(UUID(int=1))),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(2, 503, "/cancel"))
    assert await dialog.get(_dialog_key(503)) is None
    assert deps.strings.contacts_cancelled in _sent_texts(session)


@pytest.mark.unit
async def test_contacts_requires_onboarding_and_stranger_rename() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _text_update(1, 504, "/contacts"))
    assert deps.strings.age_prompt in _sent_texts(session)
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 504, "ct:n"))
    await _onboard(bot, lifecycle, 505, catalog)
    await _onboard(bot, lifecycle, 506, catalog)
    await lifecycle.dispatcher.feed_update(bot, _callback(3, 505, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(4, 505, "ct:rel:partner"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(5, 505, "Mine"))
    owner = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(505)))
    ).user
    assert owner is not None
    listed_result = await deps.list_contacts.execute(ListContactsCommand(owner.id))
    assert listed_result.contacts
    foreign = listed_result.contacts[0].id
    before = len(_sent_texts(session))
    await lifecycle.dispatcher.feed_update(bot, _callback(6, 506, f"ct:a:{foreign}"))
    after = _sent_texts(session)[before:]
    assert any(deps.strings.error_generic in text for text in after), after
    await lifecycle.dispatcher.feed_update(bot, _callback(7, 506, f"ct:r:{foreign}"))
    before = len(_sent_texts(session))
    await lifecycle.dispatcher.feed_update(bot, _text_update(8, 506, "Hijack"))
    after = _sent_texts(session)[before:]
    assert any(deps.strings.contacts_unavailable in text for text in after), after


@pytest.mark.unit
async def test_contacts_callback_parse_and_corrupt_dialog() -> None:
    assert _parse_relationship(None) is None
    assert _parse_relationship("ct:rel:nope") is None
    assert _parse_relationship("x:rel:friend") is None
    assert _parse_contact_id(None, "a") is None
    assert _parse_contact_id("ct:a:nope", "a") is None
    assert _parse_contact_id("ct:z:" + str(UUID(int=1)), "a") is None
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    dialog = FakeDialogState()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 507, catalog)
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 507, "ct:rel:nope"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 507, f"ct:a:{UUID(int=99)}"))
    await dialog.set(_dialog_key(507), DialogRecord(step="awaiting_label"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, 507, "Name"))
    assert deps.strings.error_generic in _sent_texts(session)
    await dialog.set(_dialog_key(507), DialogRecord(step="awaiting_rename"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(4, 507, "Name"))
    assert deps.strings.error_generic in _sent_texts(session)
    await dialog.set(
        _dialog_key(507),
        DialogRecord(step="awaiting_rename", contact_id=ContactId(UUID(int=99))),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(5, 507, ""))
    assert deps.strings.contacts_invalid_label in _sent_texts(session)
    await lifecycle.dispatcher.feed_update(bot, _callback(6, 507, "ct:a:nope"))
    await lifecycle.dispatcher.feed_update(bot, _callback(7, 507, "ct:r:nope"))
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=8,
            callback_query=CallbackQuery(
                id="8",
                from_user=User(id=507, is_bot=False, first_name="A"),
                chat_instance="x",
                data=f"ct:a:{UUID(int=1)}",
                message=InaccessibleMessage(
                    chat=Chat(id=507, type="private"), message_id=1, date=0
                ),
            ),
        ),
    )
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=9,
            callback_query=CallbackQuery(
                id="9",
                from_user=User(id=507, is_bot=False, first_name="A"),
                chat_instance="x",
                data="ct:rel:friend",
                message=InaccessibleMessage(
                    chat=Chat(id=507, type="private"), message_id=1, date=0
                ),
            ),
        ),
    )


class _AccessDeniedCreate:
    async def execute(self, command: CreateContactCommand) -> CreateContactResult:
        raise AccessNotGranted(
            AccessStatus(age_confirmed=True, missing_consents=frozenset(), granted=False)
        )


class _AccessDeniedRename:
    async def execute(self, command: RenameContactCommand) -> RenameContactResult:
        raise AccessNotGranted(
            AccessStatus(age_confirmed=True, missing_consents=frozenset(), granted=False)
        )


@pytest.mark.unit
async def test_rename_not_found_clears_dialog_then_decode() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    dialog = FakeDialogState()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog, generator=generator)
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 531, catalog)
    await dialog.set(
        _dialog_key(531),
        DialogRecord(step="awaiting_rename", contact_id=ContactId(UUID(int=99))),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(1, 531, "NewName"))
    assert deps.strings.contacts_unavailable in _sent_texts(session)
    assert await dialog.get(_dialog_key(531)) is None
    await lifecycle.dispatcher.feed_update(bot, _text_update(2, 531, "please decode this now"))
    assert len(generator.decode_stream_calls) == 1


@pytest.mark.unit
async def test_dialog_access_not_granted_clears_dialog() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    dialog = FakeDialogState()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog))
    deps = replace(deps, create_contact=cast(CreateContact, _AccessDeniedCreate()))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 510, catalog)
    await dialog.set(
        _dialog_key(510),
        DialogRecord(step="awaiting_label", relationship=RelationshipKind.FRIEND),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(1, 510, "Alex"))
    assert deps.strings.contacts_unavailable in _sent_texts(session)
    assert await dialog.get(_dialog_key(510)) is None
    deps = replace(deps, rename_contact=cast(RenameContact, _AccessDeniedRename()))
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await dialog.set(
        _dialog_key(510),
        DialogRecord(step="awaiting_rename", contact_id=ContactId(UUID(int=1))),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(2, 510, "Alex"))
    assert await dialog.get(_dialog_key(510)) is None


@pytest.mark.unit
async def test_polling_commands_include_contacts_and_cancel() -> None:
    deps = make_telegram_deps()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await lifecycle.start()
    await lifecycle.shutdown()
    commands = next(req for req in session.requests if isinstance(req, SetMyCommands))
    names = [item.command for item in commands.commands]
    assert "contacts" in names
    assert "cancel" in names


@pytest.mark.unit
def test_contact_keyboards_and_relationship_labels() -> None:
    strings = load_ru_strings()
    ident = ContactId(UUID(int=1))
    unpaired = Contact(
        id=ident,
        owner_id=UserId(UUID(int=2)),
        label=ContactLabel("Sam"),
        relationship=RelationshipKind.FRIEND,
        pair_id=None,
        created_at=_NOW,
    )
    keyboard = contacts_keyboard(strings, (unpaired,))
    payloads = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
    assert "ct:n" in payloads
    assert f"ct:i:{ident}" in payloads
    assert all(item is not None and "Sam" not in item for item in payloads)
    assert all(len(item.encode()) <= 64 for item in payloads if item is not None)
    paired = Contact(
        id=ContactId(UUID(int=3)),
        owner_id=UserId(UUID(int=2)),
        label=ContactLabel("Pat"),
        relationship=RelationshipKind.PARTNER,
        pair_id=PairId(UUID(int=4)),
        created_at=_NOW,
    )
    leave_kb = contacts_keyboard(strings, (paired,))
    leave_payloads = [btn.callback_data for row in leave_kb.inline_keyboard for btn in row]
    assert f"ct:l:{paired.id}" in leave_payloads
    assert f"ct:i:{paired.id}" not in leave_payloads
    rel = relationship_keyboard(strings)
    kinds = {kind.value for kind in RelationshipKind}
    found = {
        btn.callback_data.split(":")[2]
        for row in rel.inline_keyboard
        for btn in row
        if btn.callback_data is not None
    }
    assert found == kinds
    for kind in RelationshipKind:
        assert relationship_label(strings, kind)
    with pytest.raises(ValueError, match="64 bytes"):
        _require_callback_bytes("x" * 65)


@pytest.mark.unit
async def test_contact_label_sentinel_absent_from_logs(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    sink = RecordingUsageEventSink()
    generator = FakeTextGenerator()
    deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, generator=generator, sink=sink)
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 508, catalog)
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 508, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 508, "ct:rel:friend"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, 508, _LABEL_SENTINEL))
    blob = " ".join(str(event) for event in capture_log_events())
    assert _LABEL_SENTINEL not in blob
    assert generator.decode_stream_calls == []
    assert sink.events == []
    assert _LABEL_SENTINEL in _sent_texts(session)[-1]


@pytest.mark.unit
async def test_contacts_handlers_skip_missing_user_and_chat() -> None:
    deps = make_telegram_deps()
    bare = Message(
        message_id=1,
        date=_NOW,
        chat=Chat(id=1, type="private"),
        text="/contacts",
    )
    await contacts_command(bare, deps)
    assert await AwaitingDialogText()(bare, deps) is False
    await dialog_text(bare, deps)
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    dialog = FakeDialogState()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 509, catalog)
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=80,
            callback_query=CallbackQuery(
                id="80",
                from_user=User(id=509, is_bot=False, first_name="A"),
                chat_instance="x",
                data="ct:n",
                message=InaccessibleMessage(
                    chat=Chat(id=509, type="private"), message_id=1, date=0
                ),
            ),
        ),
    )
    await dialog.set(
        _dialog_key(509),
        DialogRecord(step="awaiting_label", relationship=RelationshipKind.FRIEND),
    )
    await deps.revoke_all_consents.execute(RevokeAllConsentsCommand(TelegramUserId(509)))
    await lifecycle.dispatcher.feed_update(bot, _text_update(9, 509, "AfterRevoke"))
    await dialog.set(
        _dialog_key(509),
        DialogRecord(step="awaiting_rename", contact_id=ContactId(UUID(int=1))),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(10, 509, "AfterRevoke2"))


class _HideUser:
    def __init__(self, inner: object) -> None:
        self._inner = inner

    async def execute(self, query: GetUserByTelegramIdQuery) -> GetUserByTelegramIdResult:
        await cast(Any, self._inner).execute(query)
        return GetUserByTelegramIdResult(user=None)


class _HideAfterFirst:
    def __init__(self, inner: object) -> None:
        self._inner = inner
        self._n = 0

    async def execute(self, query: GetUserByTelegramIdQuery) -> GetUserByTelegramIdResult:
        result = cast(GetUserByTelegramIdResult, await cast(Any, self._inner).execute(query))
        self._n += 1
        if self._n > 1:
            return GetUserByTelegramIdResult(user=None)
        return result


@pytest.mark.unit
async def test_contacts_onboarding_other_callbacks_and_inaccessible_rename() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    ident = UUID(int=1)
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 520, f"ct:a:{ident}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 520, f"ct:r:{ident}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(3, 520, "ct:rel:friend"))
    assert deps.strings.age_prompt in _sent_texts(session)
    await _onboard(bot, lifecycle, 521, catalog)
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=4,
            callback_query=CallbackQuery(
                id="4",
                from_user=User(id=521, is_bot=False, first_name="A"),
                chat_instance="x",
                data=f"ct:r:{ident}",
                message=InaccessibleMessage(
                    chat=Chat(id=521, type="private"), message_id=1, date=0
                ),
            ),
        ),
    )


@pytest.mark.unit
async def test_contacts_actor_missing_after_done() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 522, catalog)
    await lifecycle.dispatcher.feed_update(bot, _callback(10, 522, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(11, 522, "ct:rel:friend"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(12, 522, "Sam"))
    owner = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(522)))
    ).user
    assert owner is not None
    listed = await deps.list_contacts.execute(ListContactsCommand(owner.id))
    contact_id = listed.contacts[0].id
    after_first = replace(
        deps,
        get_user_by_telegram_id=cast(Any, _HideAfterFirst(deps.get_user_by_telegram_id)),
    )
    lifecycle = build_telegram_lifecycle(_settings(), after_first, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _callback(13, 522, f"ct:a:{contact_id}"))
    hidden = replace(
        deps, get_user_by_telegram_id=cast(Any, _HideUser(deps.get_user_by_telegram_id))
    )
    lifecycle = build_telegram_lifecycle(_settings(), hidden, bot=bot)
    await lifecycle.dispatcher.feed_update(bot, _text_update(14, 522, "/contacts"))
    assert deps.strings.age_prompt in _sent_texts(session)


@pytest.mark.unit
async def test_awaiting_rule_text_is_not_a_contacts_dialog() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    dialog = FakeDialogState()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog))
    await dialog.set(
        _dialog_key(523),
        DialogRecord(
            step="awaiting_rule_text",
            contact_id=ContactId(UUID(int=1)),
            category=None,
        ),
    )
    message = Message(
        message_id=1,
        date=_NOW,
        chat=Chat(id=523, type="private"),
        from_user=User(id=523, is_bot=False, first_name="A"),
        text="не повышать голос",
    )
    assert await AwaitingDialogText()(message, deps) is False


@pytest.mark.unit
async def test_polling_commands_include_rules() -> None:
    deps = make_telegram_deps()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await lifecycle.start()
    await lifecycle.shutdown()
    commands = next(req for req in session.requests if isinstance(req, SetMyCommands))
    names = [item.command for item in commands.commands]
    assert "rules" in names


@pytest.mark.unit
async def test_invite_leave_and_dialog_edge_branches() -> None:
    from uuid import UUID

    from svoi_pravila.application.errors import AccessNotGranted, NotFound
    from svoi_pravila.application.use_cases.create_invite import CreateInviteResult
    from svoi_pravila.application.use_cases.leave_pair import LeavePairResult
    from svoi_pravila.domain.access import AccessStatus
    from svoi_pravila.domain.ids import InviteId
    from svoi_pravila.domain.invite import Invite, InviteTokenHash

    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    dialog = FakeDialogState()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, dialog=dialog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(_settings(), deps, bot=bot)
    await _onboard(bot, lifecycle, 540, catalog)
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 540, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 540, "ct:rel:friend"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, 540, "EdgeContact"))
    owner = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(540)))
    ).user
    assert owner is not None
    contact_id = (await deps.list_contacts.execute(ListContactsCommand(owner.id))).contacts[0].id

    # confirm leave on unlinked contact
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(4, 540, f"ct:ly:{contact_id}"))
    assert deps.strings.contacts_unavailable in _sent_texts(session)[-1]

    # leave_pair error matrix
    class _LeaveBoom:
        async def execute(self, command: object) -> LeavePairResult:
            raise NotFound()

    class _LeaveDenied:
        async def execute(self, command: object) -> LeavePairResult:
            raise AccessNotGranted(
                AccessStatus(age_confirmed=True, missing_consents=frozenset(), granted=False)
            )

    # invent a paired contact id that list will not find → unavailable already covered
    # stub leave after injecting pair via HideAfterFirst on get user for confirm/set/start
    hidden = replace(
        deps, get_user_by_telegram_id=cast(Any, _HideUser(deps.get_user_by_telegram_id))
    )
    life_hidden = build_telegram_lifecycle(_settings(), hidden, bot=bot)
    await life_hidden.dispatcher.feed_update(bot, _callback(5, 540, f"ct:a:{contact_id}"))
    await life_hidden.dispatcher.feed_update(bot, _callback(6, 540, f"ct:i:{contact_id}"))
    await life_hidden.dispatcher.feed_update(bot, _callback(7, 540, f"ct:ly:{contact_id}"))

    after_first = replace(
        deps,
        get_user_by_telegram_id=cast(Any, _HideAfterFirst(deps.get_user_by_telegram_id)),
    )
    life_af = build_telegram_lifecycle(_settings(), after_first, bot=bot)
    await life_af.dispatcher.feed_update(bot, _callback(8, 540, f"ct:a:{contact_id}"))

    # start_invite long token raises
    long_token = "t" * 80
    owner_id = owner.id

    class _LongInvite:
        async def execute(self, command: object) -> CreateInviteResult:
            invite = Invite.create(
                invite_id=InviteId(UUID(int=9)),
                inviter_id=owner_id,
                contact_id=contact_id,
                token_hash=InviteTokenHash.from_raw_token("abc"),
                created_at=_NOW,
            )
            return CreateInviteResult(invite=invite, raw_token=long_token)

    long_deps = replace(deps, create_invite=cast(Any, _LongInvite()))
    life_long = build_telegram_lifecycle(_settings(), long_deps, bot=bot)
    await life_long.dispatcher.feed_update(bot, _callback(9, 540, f"ct:i:{contact_id}"))

    # ask_leave / cancel_leave / confirm_leave with no chat via inaccessible/null message
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=10,
            callback_query=CallbackQuery(
                id="10",
                from_user=User(id=540, is_bot=False, first_name="A"),
                chat_instance="x",
                data=f"ct:l:{contact_id}",
                message=None,
            ),
        ),
    )
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=11,
            callback_query=CallbackQuery(
                id="11",
                from_user=User(id=540, is_bot=False, first_name="A"),
                chat_instance="x",
                data="ct:lx",
                message=None,
            ),
        ),
    )
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=12,
            callback_query=CallbackQuery(
                id="12",
                from_user=User(id=540, is_bot=False, first_name="A"),
                chat_instance="x",
                data=f"ct:ly:{contact_id}",
                message=None,
            ),
        ),
    )

    # leave_pair raises after contact appears paired — set pair_id via AcceptInvite path is heavy;
    # stub list_contacts to return paired contact
    from svoi_pravila.application.use_cases.list_contacts import ListContactsResult
    from svoi_pravila.domain.contact import Contact
    from svoi_pravila.domain.ids import PairId
    from svoi_pravila.domain.text import ContactLabel

    paired_contact = Contact(
        id=contact_id,
        owner_id=owner.id,
        label=ContactLabel("EdgeContact"),
        relationship=RelationshipKind.FRIEND,
        pair_id=PairId(UUID(int=3)),
        created_at=_NOW,
    )

    class _ListPaired:
        async def execute(self, command: object) -> ListContactsResult:
            return ListContactsResult(contacts=(paired_contact,))

    for idx, leave_stub in enumerate((_LeaveBoom(), _LeaveDenied())):
        leave_deps = replace(
            deps,
            list_contacts=cast(Any, _ListPaired()),
            leave_pair=cast(Any, leave_stub),
        )
        session.requests.clear()
        life_leave = build_telegram_lifecycle(_settings(), leave_deps, bot=bot)
        await life_leave.dispatcher.feed_update(
            bot, _callback(30 + idx, 540, f"ct:ly:{contact_id}")
        )
        assert deps.strings.error_generic in _sent_texts(session)[-1]

    # invite label dialog edges
    await dialog.set(
        _dialog_key(540),
        DialogRecord(step="awaiting_invite_label", invite_id=None, relationship=None),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(20, 540, "BadInviteLabel"))
    await dialog.set(
        _dialog_key(540),
        DialogRecord(
            step="awaiting_invite_label",
            invite_id=InviteId(UUID(int=8)),
            relationship=RelationshipKind.FRIEND,
        ),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(21, 540, ""))  # invalid label
    await dialog.set(
        _dialog_key(540),
        DialogRecord(
            step="awaiting_invite_label",
            invite_id=InviteId(UUID(int=8)),
            relationship=RelationshipKind.FRIEND,
        ),
    )

    class _AcceptFail:
        async def execute(self, command: object) -> object:
            raise NotFound()

    accept_fail = replace(deps, accept_invite=cast(Any, _AcceptFail()))
    life_fail = build_telegram_lifecycle(_settings(), accept_fail, bot=bot)
    session.requests.clear()
    await life_fail.dispatcher.feed_update(bot, _text_update(22, 540, "ValidLabelHere"))
    assert deps.strings.invite_invalid in _sent_texts(session)[-1]

    # dialog while actor missing after record set
    await dialog.set(
        _dialog_key(540),
        DialogRecord(
            step="awaiting_invite_label",
            invite_id=InviteId(UUID(int=8)),
            relationship=RelationshipKind.FRIEND,
        ),
    )
    life_hidden2 = build_telegram_lifecycle(_settings(), hidden, bot=bot)
    await life_hidden2.dispatcher.feed_update(bot, _text_update(23, 540, "AfterHide"))

    # require_done false for dialog
    await dialog.set(
        _dialog_key(541),
        DialogRecord(
            step="awaiting_invite_label",
            invite_id=InviteId(UUID(int=8)),
            relationship=RelationshipKind.FRIEND,
        ),
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(24, 541, "NotOnboarded"))
