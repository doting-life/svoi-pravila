"""Bot pair invite, shared rules, leave, and privacy canary."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)
from tests.factories import make_settings
from tests.fakes.clock import FakeClock
from tests.fakes.consent_catalog import FakeConsentCatalog
from tests.fakes.ids import FakeIdGenerator
from tests.fakes.inline_reuse import make_inline_reuse
from tests.fakes.pair_notifier import FakePairNotifier
from tests.fakes.rate_limit import FakePseudonymizer
from tests.fakes.telegram_deps import TelegramTestDeps, make_telegram_deps
from tests.fakes.telegram_session import FakeTelegramSession
from tests.fakes.tokens import FakeTokenGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory

from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.handlers.helpers import FEATURE_CALLBACK_PREFIXES
from svoi_pravila.adapters.channels.telegram.keyboards import (
    invite_relationship_keyboard,
    invite_share_keyboard,
)
from svoi_pravila.adapters.channels.telegram.lifecycle import TelegramLifecycle
from svoi_pravila.adapters.channels.telegram.localization import load_ru_strings
from svoi_pravila.adapters.system.tokens import SecretsInviteTokenGenerator
from svoi_pravila.application.use_cases.accept_invite import AcceptInvite, AcceptInviteCommand
from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.create_invite import CreateInvite, CreateInviteCommand
from svoi_pravila.application.use_cases.delete_my_account import (
    DeleteMyAccount,
    DeleteMyAccountCommand,
    DeleteMyAccountPorts,
)
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramIdQuery
from svoi_pravila.application.use_cases.list_contacts import ListContactsCommand
from svoi_pravila.application.use_cases.resolve_invite import ResolveInvite, ResolveInviteCommand
from svoi_pravila.config import Environment, TelegramUpdatesMode
from svoi_pravila.domain.enums import ConsentKind, RelationshipKind, RuleStatus
from svoi_pravila.domain.ids import InviteId, TelegramUserId
from svoi_pravila.domain.rules import PairScope
from svoi_pravila.domain.text import ContactLabel

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_LABEL_A = "SENTINEL_LABEL_A_0016"
_LABEL_B = "SENTINEL_LABEL_B_0016"
_START_PARAM_MAX = 64


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
                text="prompt",
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


def _sent_to(session: FakeTelegramSession, user_id: int) -> list[str]:
    return [
        str(req.text)
        for req in session.requests
        if isinstance(req, SendMessage) and int(req.chat_id) == user_id
    ]


@pytest.mark.unit
def test_start_parameter_length_and_callback_formats() -> None:
    token = SecretsInviteTokenGenerator().new_invite_token()
    start_param = f"inv_{token}"
    assert len(start_param) <= _START_PARAM_MAX
    strings = load_ru_strings()
    deep = f"https://t.me/test_bot?start={start_param}"
    share = invite_share_keyboard(deep, strings)
    assert share.inline_keyboard[0][0].url is not None
    assert "share/url" in share.inline_keyboard[0][0].url
    invite_id = InviteId(UUID(int=42))
    rel = invite_relationship_keyboard(invite_id, strings)
    for row in rel.inline_keyboard:
        for button in row:
            assert button.callback_data is not None
            assert button.callback_data.startswith("iv:rel:")
            assert len(button.callback_data.encode("utf-8")) <= 64
    assert "pr" in FEATURE_CALLBACK_PREFIXES
    assert "iv" in FEATURE_CALLBACK_PREFIXES


@pytest.mark.unit
async def test_invite_accept_shared_rule_leave_and_privacy_canary() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    clock = FakeClock()
    ids = FakeIdGenerator()
    tokens = FakeTokenGenerator()
    notifier = FakePairNotifier()
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            clock=clock,
            ids=ids,
            tokens=tokens,
            pair_notifier=notifier,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.POLLING,
            telegram_bot_token="1:TEST",
        ),
        deps,
        bot=bot,
    )
    inviter_tg = 801
    invitee_tg = 802
    await _onboard(bot, lifecycle, inviter_tg, catalog)
    await _onboard(bot, lifecycle, invitee_tg, catalog)

    await lifecycle.dispatcher.feed_update(bot, _callback(1, inviter_tg, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, inviter_tg, "ct:rel:partner"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, inviter_tg, _LABEL_A))
    inviter = (
        await deps.get_user_by_telegram_id.execute(
            GetUserByTelegramIdQuery(TelegramUserId(inviter_tg))
        )
    ).user
    assert inviter is not None
    listed = await deps.list_contacts.execute(ListContactsCommand(inviter.id))
    contact_id = listed.contacts[0].id

    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(4, inviter_tg, f"ct:i:{contact_id}"))
    invite_msgs = _sent_to(session, inviter_tg)
    assert any("t.me/test_bot?start=inv_" in text for text in invite_msgs)
    assert any(load_ru_strings().contacts_invite_explain in text for text in invite_msgs)
    share_req = next(r for r in session.requests if isinstance(r, SendMessage) and r.reply_markup)
    assert isinstance(share_req.reply_markup, InlineKeyboardMarkup)
    share_url = share_req.reply_markup.inline_keyboard[0][0].url
    assert share_url is not None
    deep = unquote(parse_qs(urlparse(share_url).query)["url"][0])
    start_param = parse_qs(urlparse(deep).query)["start"][0]
    assert start_param.startswith("inv_")
    assert len(start_param) <= _START_PARAM_MAX
    raw_token = start_param.removeprefix("inv_")

    invitee = (
        await deps.get_user_by_telegram_id.execute(
            GetUserByTelegramIdQuery(TelegramUserId(invitee_tg))
        )
    ).user
    assert invitee is not None
    resolved = await ResolveInvite(uow, catalog, clock).execute(
        ResolveInviteCommand(invitee.id, raw_token)
    )

    session.requests.clear()
    await lifecycle.dispatcher.feed_update(
        bot, _text_update(5, invitee_tg, f"/start {start_param}")
    )
    assert any(
        load_ru_strings().invite_relationship_prompt in text
        for text in _sent_to(session, invitee_tg)
    )
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(
        bot, _callback(6, invitee_tg, f"iv:rel:{resolved.invite_id}:friend")
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(7, invitee_tg, _LABEL_B))
    invitee_texts = _sent_to(session, invitee_tg)
    assert any(load_ru_strings().invite_accepted_invitee in text for text in invitee_texts)
    for text in invitee_texts:
        assert _LABEL_A not in text
    assert notifier.invite_accepted_calls

    await lifecycle.dispatcher.feed_update(bot, _text_update(8, inviter_tg, "/rules"))
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(9, inviter_tg, "ru:n"))
    assert any(
        load_ru_strings().rules_scope_prompt in text for text in _sent_to(session, inviter_tg)
    )
    await lifecycle.dispatcher.feed_update(bot, _callback(10, inviter_tg, "ru:ss"))
    await lifecycle.dispatcher.feed_update(bot, _callback(11, inviter_tg, "ru:cat:other"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(12, inviter_tg, "shared rule text"))
    assert notifier.shared_rule_proposed_calls
    rule_id = notifier.shared_rule_proposed_calls[-1].rule_id
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(13, invitee_tg, f"pr:y:{rule_id}"))
    assert notifier.shared_rule_decided_calls
    approved_decision = notifier.shared_rule_decided_calls[-1]
    assert approved_decision.approved is True
    async with uow() as unit:
        rule = await unit.rules.get(rule_id)
        assert rule is not None
        assert rule.status is RuleStatus.ACTIVE
        assert isinstance(rule.scope, PairScope)

    # Reject path with a second shared rule
    await lifecycle.dispatcher.feed_update(bot, _callback(20, inviter_tg, "ru:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(21, inviter_tg, "ru:ss"))
    await lifecycle.dispatcher.feed_update(bot, _callback(22, inviter_tg, "ru:cat:other"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(23, inviter_tg, "to reject"))
    reject_id = notifier.shared_rule_proposed_calls[-1].rule_id
    await lifecycle.dispatcher.feed_update(bot, _callback(24, invitee_tg, f"pr:n:{reject_id}"))
    rejected_decision = notifier.shared_rule_decided_calls[-1]
    assert rejected_decision.approved is False

    inviter_contacts = await deps.list_contacts.execute(ListContactsCommand(inviter.id))
    paired = next(c for c in inviter_contacts.contacts if c.pair_id is not None)
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(14, inviter_tg, f"ct:l:{paired.id}"))
    assert any(
        load_ru_strings().contacts_leave_confirm in text for text in _sent_to(session, inviter_tg)
    )
    await lifecycle.dispatcher.feed_update(bot, _callback(15, inviter_tg, f"ct:ly:{paired.id}"))
    assert notifier.partner_left_calls

    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _text_update(16, inviter_tg, "/help"))
    assert any("приглас" in text.lower() for text in _sent_to(session, inviter_tg))


@pytest.mark.unit
async def test_start_invite_before_onboarding_asks_to_reopen() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    deps = make_telegram_deps(TelegramTestDeps(uow=uow, catalog=catalog))
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.POLLING,
            telegram_bot_token="1:TEST",
        ),
        deps,
        bot=bot,
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(1, 900, "/start inv_abc"))
    texts = [str(req.text) for req in session.requests if isinstance(req, SendMessage)]
    assert any(load_ru_strings().invite_reopen_link in text for text in texts)


@pytest.mark.unit
async def test_delete_while_paired_notifies_partner() -> None:
    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    clock = FakeClock()
    ids = FakeIdGenerator()
    tokens = FakeTokenGenerator()
    notifier = FakePairNotifier()
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            clock=clock,
            ids=ids,
            tokens=tokens,
            pair_notifier=notifier,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    lifecycle = build_telegram_lifecycle(
        make_settings(
            environment=Environment.LOCAL,
            telegram_updates_mode=TelegramUpdatesMode.POLLING,
            telegram_bot_token="1:TEST",
        ),
        deps,
        bot=bot,
    )
    await _onboard(bot, lifecycle, 901, catalog)
    await _onboard(bot, lifecycle, 902, catalog)
    inviter = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(901)))
    ).user
    invitee = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(902)))
    ).user
    assert inviter is not None and invitee is not None
    contact = (
        await CreateContact(uow, catalog, ids, clock).execute(
            CreateContactCommand(inviter.id, ContactLabel("X"), RelationshipKind.FRIEND)
        )
    ).contact
    created = await CreateInvite(uow, catalog, ids, tokens, clock).execute(
        CreateInviteCommand(inviter.id, contact.id)
    )
    await AcceptInvite(uow, catalog, ids, clock, notifier).execute(
        AcceptInviteCommand(
            invitee.id, created.invite.id, ContactLabel("Y"), RelationshipKind.FRIEND
        )
    )
    notifier.partner_left_calls.clear()
    await DeleteMyAccount(
        DeleteMyAccountPorts(
            uow, ids, FakePseudonymizer(), clock, make_inline_reuse(clock), notifier
        )
    ).execute(DeleteMyAccountCommand(TelegramUserId(901)))
    assert notifier.partner_left_calls


@pytest.mark.unit
async def test_leave_cancel_invite_errors_and_accept_matrix() -> None:
    from dataclasses import replace
    from typing import Any, cast
    from uuid import UUID

    from svoi_pravila.adapters.channels.telegram.handlers.contacts import _try_accept_invite
    from svoi_pravila.application.errors import (
        AccessNotGranted,
        AlreadyPaired,
        ContactAlreadyLinked,
        ContactLimitReached,
        NotFound,
    )
    from svoi_pravila.domain.access import AccessStatus
    from svoi_pravila.domain.errors import (
        InviteAlreadyAcceptedError,
        InviteExpiredError,
        SelfInviteAcceptError,
    )
    from svoi_pravila.domain.ids import InviteId, UserId
    from svoi_pravila.domain.user import User as DomainUser

    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    clock = FakeClock()
    ids = FakeIdGenerator()
    tokens = FakeTokenGenerator()
    notifier = FakePairNotifier()
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            clock=clock,
            ids=ids,
            tokens=tokens,
            pair_notifier=notifier,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    inviter_tg = 811
    invitee_tg = 812
    await _onboard(bot, lifecycle, inviter_tg, catalog)
    await _onboard(bot, lifecycle, invitee_tg, catalog)
    await lifecycle.dispatcher.feed_update(bot, _callback(1, inviter_tg, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, inviter_tg, "ct:rel:friend"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, inviter_tg, "LeaveCancelA"))
    inviter = (
        await deps.get_user_by_telegram_id.execute(
            GetUserByTelegramIdQuery(TelegramUserId(inviter_tg))
        )
    ).user
    assert inviter is not None
    contact_id = (await deps.list_contacts.execute(ListContactsCommand(inviter.id))).contacts[0].id
    await lifecycle.dispatcher.feed_update(bot, _callback(4, inviter_tg, f"ct:i:{contact_id}"))
    deep = next(t for t in _sent_to(session, inviter_tg) if "start=inv_" in t)
    start_param = parse_qs(urlparse(deep.split("\n", 1)[0]).query)["start"][0]
    raw_token = start_param.removeprefix("inv_")
    invitee = (
        await deps.get_user_by_telegram_id.execute(
            GetUserByTelegramIdQuery(TelegramUserId(invitee_tg))
        )
    ).user
    assert invitee is not None
    resolved = await ResolveInvite(uow, catalog, clock).execute(
        ResolveInviteCommand(invitee.id, raw_token)
    )
    await lifecycle.dispatcher.feed_update(
        bot, _text_update(5, invitee_tg, f"/start {start_param}")
    )
    await lifecycle.dispatcher.feed_update(
        bot, _callback(6, invitee_tg, f"iv:rel:{resolved.invite_id}:friend")
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(7, invitee_tg, "LeaveCancelB"))
    invitee_contacts = await deps.list_contacts.execute(ListContactsCommand(invitee.id))
    paired_id = invitee_contacts.contacts[0].id
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(8, invitee_tg, f"ct:l:{paired_id}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(9, invitee_tg, "ct:lx"))
    assert _sent_to(session, invitee_tg)
    await lifecycle.dispatcher.feed_update(bot, _callback(10, invitee_tg, "ct:l:not-a-uuid"))
    await lifecycle.dispatcher.feed_update(bot, _callback(11, invitee_tg, "ct:ly:not-a-uuid"))
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=12,
            callback_query=CallbackQuery(
                id="12",
                from_user=User(id=invitee_tg, is_bot=False, first_name="A"),
                chat_instance="x",
                data=f"ct:l:{paired_id}",
                message=None,
            ),
        ),
    )
    await lifecycle.dispatcher.feed_update(bot, _callback(13, 899, f"ct:l:{paired_id}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(14, 899, "ct:lx"))
    await lifecycle.dispatcher.feed_update(bot, _callback(15, inviter_tg, f"ct:i:{UUID(int=99)}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(16, inviter_tg, f"ct:ly:{contact_id}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(17, invitee_tg, "ct:i:not-a-uuid"))
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=18,
            callback_query=CallbackQuery(
                id="18",
                from_user=User(id=inviter_tg, is_bot=False, first_name="A"),
                chat_instance="x",
                data=f"ct:i:{contact_id}",
                message=None,
            ),
        ),
    )

    class _LinkedInvite:
        async def execute(self, command: object) -> object:
            raise ContactAlreadyLinked()

    linked = replace(deps, create_invite=cast(Any, _LinkedInvite()))
    life_linked = build_telegram_lifecycle(settings, linked, bot=bot)
    session.requests.clear()
    await life_linked.dispatcher.feed_update(bot, _callback(19, invitee_tg, f"ct:i:{paired_id}"))
    assert deps.strings.contacts_unavailable in _sent_to(session, invitee_tg)[-1]

    class _AcceptRaising:
        def __init__(self, exc: BaseException) -> None:
            self._exc = exc

        async def execute(self, command: object) -> object:
            raise self._exc

    domain_user = DomainUser(
        id=UserId(UUID(int=50)),
        telegram_user_id=TelegramUserId(50),
        created_at=_NOW,
        age_confirmed_at=_NOW,
        active_contact_id=None,
    )
    invite = InviteId(UUID(int=77))
    label = ContactLabel("X")
    denied = AccessNotGranted(
        AccessStatus(age_confirmed=True, missing_consents=frozenset(), granted=False)
    )
    cases: list[tuple[BaseException, str]] = [
        (ContactLimitReached(), deps.strings.contacts_limit),
        (NotFound(), deps.strings.invite_invalid),
        (InviteAlreadyAcceptedError("x"), deps.strings.invite_invalid),
        (InviteExpiredError("x"), deps.strings.invite_expired),
        (SelfInviteAcceptError("x"), deps.strings.invite_own),
        (AlreadyPaired(), deps.strings.error_generic),
        (denied, deps.strings.error_generic),
    ]
    for exc, expected in cases:
        text = await _try_accept_invite(
            replace(deps, accept_invite=cast(Any, _AcceptRaising(exc))),
            domain_user,
            invite_id=invite,
            relationship=RelationshipKind.FRIEND,
            label=label,
        )
        assert text == expected

    class _AcceptOk:
        async def execute(self, command: object) -> object:
            return object()

    assert (
        await _try_accept_invite(
            replace(deps, accept_invite=cast(Any, _AcceptOk())),
            domain_user,
            invite_id=invite,
            relationship=RelationshipKind.FRIEND,
            label=label,
        )
        is None
    )


@pytest.mark.unit
async def test_invite_resolve_errors_and_relationship_gates() -> None:
    from dataclasses import replace
    from typing import Any, cast
    from uuid import UUID

    from svoi_pravila.adapters.channels.telegram.handlers.onboarding import (
        _parse_invite_relationship,
    )
    from svoi_pravila.application.errors import AccessNotGranted, NotFound
    from svoi_pravila.application.use_cases.resolve_invite import ResolveInviteResult
    from svoi_pravila.domain.access import AccessStatus
    from svoi_pravila.domain.errors import (
        InviteAlreadyAcceptedError,
        InviteExpiredError,
        SelfInviteAcceptError,
    )

    assert _parse_invite_relationship(None) is None
    assert _parse_invite_relationship("bad") is None
    assert _parse_invite_relationship("iv:rel:not-uuid:friend") is None
    assert _parse_invite_relationship(f"iv:rel:{UUID(int=1)}:nope") is None
    parsed = _parse_invite_relationship(f"iv:rel:{UUID(int=1)}:friend")
    assert parsed is not None
    assert parsed[1] is RelationshipKind.FRIEND

    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    clock = FakeClock()
    ids = FakeIdGenerator()
    tokens = FakeTokenGenerator()
    deps = make_telegram_deps(
        TelegramTestDeps(uow=uow, catalog=catalog, clock=clock, ids=ids, tokens=tokens)
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    await _onboard(bot, lifecycle, 821, catalog)
    await lifecycle.dispatcher.feed_update(bot, _callback(1, 821, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, 821, "ct:rel:friend"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, 821, "ResolveOwner"))
    owner = (
        await deps.get_user_by_telegram_id.execute(GetUserByTelegramIdQuery(TelegramUserId(821)))
    ).user
    assert owner is not None
    listed = await deps.list_contacts.execute(ListContactsCommand(owner.id))
    created = await deps.create_invite.execute(CreateInviteCommand(owner.id, listed.contacts[0].id))
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(
        bot, _text_update(4, 821, f"/start inv_{created.raw_token}")
    )
    assert deps.strings.invite_own in _sent_to(session, 821)[-1]
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(
        bot, _text_update(5, 822, f"/start inv_{created.raw_token}")
    )
    assert any(deps.strings.invite_reopen_link in t for t in _sent_to(session, 822))
    await lifecycle.dispatcher.feed_update(
        bot, _callback(6, 822, f"iv:rel:{created.invite.id}:friend")
    )
    await lifecycle.dispatcher.feed_update(bot, _callback(7, 821, "iv:rel:bad"))

    class _ResolveRaising:
        def __init__(self, exc: BaseException) -> None:
            self._exc = exc

        async def execute(self, command: object) -> ResolveInviteResult:
            raise self._exc

    await _onboard(bot, lifecycle, 823, catalog)
    for idx, (exc, needle) in enumerate(
        (
            (NotFound(), deps.strings.invite_invalid),
            (InviteAlreadyAcceptedError("a"), deps.strings.invite_invalid),
            (
                AccessNotGranted(
                    AccessStatus(age_confirmed=True, missing_consents=frozenset(), granted=False)
                ),
                deps.strings.invite_invalid,
            ),
            (InviteExpiredError("e"), deps.strings.invite_expired),
            (SelfInviteAcceptError("o"), deps.strings.invite_own),
        )
    ):
        session.requests.clear()
        broken = replace(deps, resolve_invite=cast(Any, _ResolveRaising(exc)))
        life = build_telegram_lifecycle(settings, broken, bot=bot)
        await life.dispatcher.feed_update(bot, _text_update(50 + idx, 823, "/start inv_deadtoken"))
        assert any(needle in t for t in _sent_to(session, 823)), needle


@pytest.mark.unit
async def test_rules_private_scope_and_pending_decided_gone() -> None:
    from dataclasses import replace
    from typing import Any, cast
    from uuid import UUID

    from svoi_pravila.adapters.channels.telegram.handlers.rules import _parse_pending_rule_id
    from svoi_pravila.application.errors import AccessNotGranted, NotFound
    from svoi_pravila.application.use_cases.approve_rule import ApproveRuleResult
    from svoi_pravila.application.use_cases.reject_pending_rule import RejectPendingRuleResult
    from svoi_pravila.domain.access import AccessStatus
    from svoi_pravila.domain.errors import InvalidTransitionError
    from svoi_pravila.domain.ids import RuleId

    assert _parse_pending_rule_id(None, "y") is None
    assert _parse_pending_rule_id("bad", "y") is None
    assert _parse_pending_rule_id("pr:yy:x", "y") is None
    assert _parse_pending_rule_id(f"pr:y:{UUID(int=1)}", "y") == RuleId(UUID(int=1))
    assert _parse_pending_rule_id("pr:y:not-uuid", "y") is None

    uow = InMemoryUnitOfWorkFactory()
    catalog = FakeConsentCatalog()
    clock = FakeClock()
    ids = FakeIdGenerator()
    tokens = FakeTokenGenerator()
    notifier = FakePairNotifier()
    deps = make_telegram_deps(
        TelegramTestDeps(
            uow=uow,
            catalog=catalog,
            clock=clock,
            ids=ids,
            tokens=tokens,
            pair_notifier=notifier,
        )
    )
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    settings = make_settings(
        environment=Environment.LOCAL,
        telegram_updates_mode=TelegramUpdatesMode.POLLING,
        telegram_bot_token="1:TEST",
    )
    lifecycle = build_telegram_lifecycle(settings, deps, bot=bot)
    inviter_tg = 831
    invitee_tg = 832
    await _onboard(bot, lifecycle, inviter_tg, catalog)
    await _onboard(bot, lifecycle, invitee_tg, catalog)
    await lifecycle.dispatcher.feed_update(bot, _callback(1, inviter_tg, "ct:n"))
    await lifecycle.dispatcher.feed_update(bot, _callback(2, inviter_tg, "ct:rel:friend"))
    await lifecycle.dispatcher.feed_update(bot, _text_update(3, inviter_tg, "ScopePrivA"))
    inviter = (
        await deps.get_user_by_telegram_id.execute(
            GetUserByTelegramIdQuery(TelegramUserId(inviter_tg))
        )
    ).user
    assert inviter is not None
    contact_id = (await deps.list_contacts.execute(ListContactsCommand(inviter.id))).contacts[0].id
    await lifecycle.dispatcher.feed_update(bot, _callback(4, inviter_tg, f"ct:i:{contact_id}"))
    deep = next(t for t in _sent_to(session, inviter_tg) if "start=inv_" in t)
    start_param = parse_qs(urlparse(deep.split("\n", 1)[0]).query)["start"][0]
    raw_token = start_param.removeprefix("inv_")
    invitee = (
        await deps.get_user_by_telegram_id.execute(
            GetUserByTelegramIdQuery(TelegramUserId(invitee_tg))
        )
    ).user
    assert invitee is not None
    resolved = await ResolveInvite(uow, catalog, clock).execute(
        ResolveInviteCommand(invitee.id, raw_token)
    )
    await lifecycle.dispatcher.feed_update(
        bot, _text_update(5, invitee_tg, f"/start {start_param}")
    )
    await lifecycle.dispatcher.feed_update(
        bot, _callback(6, invitee_tg, f"iv:rel:{resolved.invite_id}:friend")
    )
    await lifecycle.dispatcher.feed_update(bot, _text_update(7, invitee_tg, "ScopePrivB"))
    await lifecycle.dispatcher.feed_update(bot, _callback(8, inviter_tg, f"ct:a:{contact_id}"))
    session.requests.clear()
    await lifecycle.dispatcher.feed_update(bot, _callback(9, inviter_tg, "ru:n"))
    assert any(deps.strings.rules_scope_prompt in t for t in _sent_to(session, inviter_tg))
    await lifecycle.dispatcher.feed_update(bot, _callback(10, inviter_tg, "ru:sp"))
    assert any(
        isinstance(r.reply_markup, InlineKeyboardMarkup)
        for r in session.requests
        if isinstance(r, SendMessage) and int(r.chat_id) == inviter_tg
    )
    await lifecycle.dispatcher.feed_update(bot, _callback(11, 899, "ru:sp"))
    await lifecycle.dispatcher.feed_update(
        bot,
        Update(
            update_id=12,
            callback_query=CallbackQuery(
                id="12",
                from_user=User(id=inviter_tg, is_bot=False, first_name="A"),
                chat_instance="x",
                data="ru:sp",
                message=None,
            ),
        ),
    )

    class _ApproveGone:
        async def execute(self, command: object) -> ApproveRuleResult:
            raise NotFound()

    class _RejectGone:
        async def execute(self, command: object) -> RejectPendingRuleResult:
            raise InvalidTransitionError("gone")

    class _RejectAccess:
        async def execute(self, command: object) -> RejectPendingRuleResult:
            raise AccessNotGranted(
                AccessStatus(age_confirmed=True, missing_consents=frozenset(), granted=False)
            )

    rid = RuleId(UUID(int=55))
    cases = (
        (40, f"pr:y:{rid}", "approve_rule", _ApproveGone()),
        (41, f"pr:n:{rid}", "reject_pending_rule", _RejectGone()),
        (42, f"pr:n:{rid}", "reject_pending_rule", _RejectAccess()),
    )
    for update_id, data, stub_field, stub in cases:
        session.requests.clear()
        broken = replace(deps, **{stub_field: cast(Any, stub)})
        life = build_telegram_lifecycle(settings, broken, bot=bot)
        await life.dispatcher.feed_update(bot, _callback(update_id, invitee_tg, data))
        texts = _sent_to(session, invitee_tg)
        assert texts, f"no messages for {data}"
        assert deps.strings.pair_rule_decided_gone in texts[-1]
    await lifecycle.dispatcher.feed_update(bot, _callback(43, invitee_tg, "pr:y:not-uuid"))
    await lifecycle.dispatcher.feed_update(bot, _callback(44, 899, f"pr:y:{rid}"))
    await lifecycle.dispatcher.feed_update(bot, _callback(45, 899, f"pr:n:{rid}"))
