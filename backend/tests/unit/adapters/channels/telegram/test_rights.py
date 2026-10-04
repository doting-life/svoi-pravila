"""Telegram /revoke /delete /export and confirmation callbacks."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.methods import SendDocument, SendMessage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    Chat,
    InaccessibleMessage,
    Message,
    Update,
    User,
)
from tests.factories import make_settings
from tests.fakes.confirmation import FakeConfirmationTokens
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.uow import InMemoryUnitOfWorkFactory

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.handlers import rights as rights_handlers
from svoi_pravila.adapters.channels.telegram.handlers.helpers import callback_chat_id
from svoi_pravila.adapters.channels.telegram.keyboards import confirm_keyboard
from svoi_pravila.adapters.channels.telegram.lifecycle import TelegramLifecycle
from svoi_pravila.application.errors import OpenRuleLimitReached
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.delete_my_account import (
    DeleteMyAccount,
    DeleteMyAccountCommand,
    DeleteMyAccountResult,
)
from svoi_pravila.application.use_cases.get_onboarding_step import (
    GetOnboardingStepQuery,
    OnboardingStepKind,
)
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramIdQuery
from svoi_pravila.config import Environment, TelegramUpdatesMode
from svoi_pravila.domain.enums import ConsentKind, RelationshipKind
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.text import ContactLabel

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_EXPORT_SENTINEL = "SENTINEL_EXPORT_RIGHTS_0006_3"


def _private_message(update_id: int, user_id: int, text: str) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=1,
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
                text="prompt",
            ),
        ),
    )


def _lifecycle(deps: TelegramDeps) -> tuple[TelegramLifecycle, Bot, FakeTelegramSession]:
    session = FakeTelegramSession()
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    return lifecycle, bot, session


async def _complete_onboarding(
    lifecycle: TelegramLifecycle,
    bot: Bot,
    catalog: FakeConsentCatalog,
    user_id: int,
) -> None:
    await lifecycle.dispatcher.feed_update(bot, _private_message(1, user_id, "/start"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, user_id, "age:y"))
    pd = catalog.current_requirement().for_kind(ConsentKind.PERSONAL_DATA).version
    sc = catalog.current_requirement().for_kind(ConsentKind.SPECIAL_CATEGORY).version
    await lifecycle.dispatcher.feed_update(bot, _callback(3, user_id, f"cg:personal_data:{pd}:y"))
    await lifecycle.dispatcher.feed_update(
        bot, _callback(4, user_id, f"cg:special_category:{sc}:y")
    )


@pytest.mark.unit
def test_confirm_keyboard_bounds() -> None:
    strings = make_telegram_deps().strings
    markup = confirm_keyboard(strings, action="rv", token="a" * 32)
    yes = markup.inline_keyboard[0][0].callback_data
    assert yes is not None
    assert len(yes.encode("utf-8")) <= 64
    with pytest.raises(ValueError, match="64 bytes"):
        confirm_keyboard(strings, action="rv", token="a" * 64)


@pytest.mark.unit
def test_parse_confirm_edges() -> None:
    assert rights_handlers._parse_confirm(None) is None
    assert rights_handlers._parse_confirm("cf:x") is None
    assert rights_handlers._parse_confirm("cf:no:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") is None
    assert rights_handlers._parse_confirm("cf:rv:ZZ") is None
    token = "ab" * 16
    assert rights_handlers._parse_confirm(f"cf:rv:{token}") == ("rv", token)


@pytest.mark.unit
async def test_export_visible_only_and_buffered(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    ids = FakeIdGenerator()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, ids=ids))
    lifecycle, bot, session = _lifecycle(deps)
    await _complete_onboarding(lifecycle, bot, catalog, 501)
    lookup = await deps.get_user_by_telegram_id.execute(
        GetUserByTelegramIdQuery(TelegramUserId(501))
    )
    assert lookup.user is not None
    await CreateContact(uow, catalog, ids, deps.clock).execute(
        CreateContactCommand(
            lookup.user.id, ContactLabel(_EXPORT_SENTINEL), RelationshipKind.FRIEND
        )
    )
    await lifecycle.dispatcher.feed_update(bot, _private_message(20, 501, "/export"))
    documents = [req for req in session.requests if isinstance(req, SendDocument)]
    assert len(documents) == 1
    sent = documents[0].document
    assert isinstance(sent, BufferedInputFile)
    assert sent.filename == "svoi-pravila-export-20260101.json"
    payload = json.loads(bytes(sent.data).decode("utf-8"))
    assert payload["export_version"] == 1
    assert _EXPORT_SENTINEL in json.dumps(payload, ensure_ascii=False)
    dumped = json.dumps(capture_log_events())
    assert _EXPORT_SENTINEL not in dumped


@pytest.mark.unit
async def test_export_unknown_user() -> None:
    deps = make_telegram_deps()
    lifecycle, bot, session = _lifecycle(deps)
    await lifecycle.dispatcher.feed_update(bot, _private_message(1, 12, "/export"))
    texts = [req.text for req in session.requests if isinstance(req, SendMessage)]
    assert deps.strings.rights_export_empty in texts


@pytest.mark.unit
async def test_confirm_stale_replay_cancel_other_user() -> None:
    tokens = FakeConfirmationTokens()
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, confirmation=tokens))
    lifecycle, bot, session = _lifecycle(deps)
    await _complete_onboarding(lifecycle, bot, catalog, 601)
    await lifecycle.dispatcher.feed_update(bot, _private_message(10, 601, "/revoke"))
    pseudo = deps.pseudonymizer.pseudonymize("rate_limit", "601")
    token = await tokens.issue(pseudonym=pseudo, action="rv")
    await lifecycle.dispatcher.feed_update(bot, _callback(11, 601, f"cf:rv:{'0' * 32}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(12, 602, f"cf:rv:{token}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(13, 601, "cx:rv"))
    await lifecycle.dispatcher.feed_update(bot, _private_message(14, 601, "/revoke"))
    token2 = await tokens.issue(pseudonym=pseudo, action="rv")
    await lifecycle.dispatcher.feed_update(bot, _callback(15, 601, f"cf:rv:{token2}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(16, 601, f"cf:rv:{token2}"))
    texts = [str(req.text) for req in session.requests if isinstance(req, SendMessage)]
    rejected = [item for item in texts if item == deps.strings.rights_confirm_rejected]
    assert len(rejected) >= 3
    assert deps.strings.rights_cancelled in texts


@pytest.mark.unit
async def test_revoke_returns_to_consent_and_blocks_decode() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    tokens = FakeConfirmationTokens()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, confirmation=tokens))
    lifecycle, bot, session = _lifecycle(deps)
    await _complete_onboarding(lifecycle, bot, catalog, 701)
    await lifecycle.dispatcher.feed_update(bot, _private_message(30, 701, "/revoke"))
    pseudo = deps.pseudonymizer.pseudonymize("rate_limit", "701")
    token = await tokens.issue(pseudonym=pseudo, action="rv")
    await lifecycle.dispatcher.feed_update(bot, _callback(31, 701, f"cf:rv:{token}"))
    step = await deps.get_onboarding_step.execute(GetOnboardingStepQuery(TelegramUserId(701)))
    assert step.step.kind is OnboardingStepKind.CONSENT
    await lifecycle.dispatcher.feed_update(bot, _private_message(32, 701, "decode me"))
    texts = [str(req.text) for req in session.requests if isinstance(req, SendMessage)]
    assert not any("analysis" in item for item in texts[-3:])

    await lifecycle.dispatcher.feed_update(bot, _private_message(40, 701, "/delete"))
    del_token = await tokens.issue(
        pseudonym=deps.pseudonymizer.pseudonymize("rate_limit", "701"),
        action="dl",
    )
    await lifecycle.dispatcher.feed_update(bot, _callback(41, 701, f"cf:dl:{del_token}"))
    assert deps.strings.rights_deleted in [
        str(req.text) for req in session.requests if isinstance(req, SendMessage)
    ]


@pytest.mark.unit
async def test_confirm_callback_malformed() -> None:
    deps = make_telegram_deps()
    lifecycle, bot, session = _lifecycle(deps)
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 9, "cf:rv:not-hex-token-value-here!!"))
    texts = [str(req.text) for req in session.requests if isinstance(req, SendMessage)]
    assert deps.strings.rights_confirm_rejected in texts


@pytest.mark.unit
async def test_rights_prompt_ignores_missing_from_user() -> None:
    deps = make_telegram_deps()
    message = Message(
        message_id=1,
        date=_NOW,
        chat=Chat(id=1, type="private"),
        text="/revoke",
    )
    await rights_handlers._prompt_confirm(message, deps, "rv", "explain")
    assert rights_handlers._parse_confirm("cf:dl:" + "ab" * 16) == ("dl", "ab" * 16)


@pytest.mark.unit
async def test_rights_handlers_skip_missing_from_user() -> None:
    deps = make_telegram_deps()
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    message = Message(
        message_id=1,
        date=_NOW,
        chat=Chat(id=1, type="private"),
        text="/export",
    )
    router = rights_handlers.build_rights_router()
    for handler in router.message.handlers:
        names = handler.callback.__code__.co_varnames
        if "bot" in names:
            await handler.callback(message, tg_deps=deps, bot=bot)
        else:
            await handler.callback(message, tg_deps=deps)


@pytest.mark.unit
def test_chat_id_none_for_inaccessible_message() -> None:
    callback = CallbackQuery(
        id="1",
        from_user=User(id=9, is_bot=False, first_name="A"),
        chat_instance="x",
        data="cf:rv:" + "0" * 32,
        message=InaccessibleMessage(chat=Chat(id=9, type="private"), message_id=1, date=0),
    )
    assert callback_chat_id(callback) is None


class _BoomDelete(DeleteMyAccount):
    def __init__(self) -> None:
        pass

    async def execute(self, command: DeleteMyAccountCommand) -> DeleteMyAccountResult:
        raise OpenRuleLimitReached()


@pytest.mark.unit
async def test_delete_confirm_open_rule_limit() -> None:
    tokens = FakeConfirmationTokens()
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog, confirmation=tokens))
    deps = replace(deps, delete_my_account=_BoomDelete())
    lifecycle, bot, session = _lifecycle(deps)
    await _complete_onboarding(lifecycle, bot, catalog, 801)
    await lifecycle.dispatcher.feed_update(bot, _private_message(50, 801, "/delete"))
    token = await tokens.issue(
        pseudonym=deps.pseudonymizer.pseudonymize("rate_limit", "801"),
        action="dl",
    )
    await lifecycle.dispatcher.feed_update(bot, _callback(51, 801, f"cf:dl:{token}"))
    texts = [str(req.text) for req in session.requests if isinstance(req, SendMessage)]
    assert deps.strings.error_generic in texts
