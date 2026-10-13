"""RedeemComposeHandoff use-case tests."""

from __future__ import annotations

from datetime import timedelta

import pytest

from svoi_pravila.application.compose_handoff import ComposeHandoffPayload
from svoi_pravila.application.compose_session import (
    COMPOSE_SESSION_TTL_SECONDS,
    ComposeSessionPayload,
)
from svoi_pravila.application.errors import (
    AccessNotGranted,
    ComposeHandoffUnavailable,
    RateLimited,
)
from svoi_pravila.application.use_cases.redeem_compose_handoff import (
    RedeemComposeHandoff,
    RedeemComposeHandoffCommand,
    RedeemComposeHandoffPorts,
)
from tests.fakes.compose_handoffs import FakeComposeHandoffs
from tests.fakes.compose_sessions import FakeComposeSessions
from tests.fakes.rate_limit import FakePseudonymizer, FakeRateLimiter
from tests.unit.application.conftest import AppWorld


def _uc(
    world: AppWorld,
    *,
    handoffs: FakeComposeHandoffs | None = None,
    sessions: FakeComposeSessions | None = None,
    rate_limiter: FakeRateLimiter | None = None,
) -> RedeemComposeHandoff:
    return RedeemComposeHandoff(
        RedeemComposeHandoffPorts(
            uow_factory=world.uow_factory,
            catalog=world.catalog,
            compose_handoffs=handoffs or FakeComposeHandoffs(),
            compose_sessions=sessions or FakeComposeSessions(),
            rate_limiter=rate_limiter or FakeRateLimiter(limit=120),
            pseudonymizer=FakePseudonymizer(),
            clock=world.clock,
        )
    )


@pytest.mark.unit
async def test_redeem_issues_session_without_contact(world: AppWorld) -> None:
    user = await world.ensure_granted_user(200)
    handoffs = FakeComposeHandoffs()
    sessions = FakeComposeSessions()
    token = await handoffs.store(ComposeHandoffPayload(user_id=user.id, draft="draft-a"))
    result = await _uc(world, handoffs=handoffs, sessions=sessions).execute(
        RedeemComposeHandoffCommand(token)
    )
    assert result.draft == "draft-a"
    assert result.session_token.startswith("cs_")
    assert sessions.items[result.session_token] == ComposeSessionPayload(user_id=user.id)
    assert result.expires_at == world.clock.now() + timedelta(seconds=COMPOSE_SESSION_TTL_SECONDS)


@pytest.mark.unit
async def test_redeem_is_single_use(world: AppWorld) -> None:
    user = await world.ensure_granted_user(202)
    handoffs = FakeComposeHandoffs()
    token = await handoffs.store(ComposeHandoffPayload(user_id=user.id, draft="solo"))
    uc = _uc(world, handoffs=handoffs)
    await uc.execute(RedeemComposeHandoffCommand(token))
    with pytest.raises(ComposeHandoffUnavailable):
        await uc.execute(RedeemComposeHandoffCommand(token))


@pytest.mark.unit
async def test_redeem_rate_limited(world: AppWorld) -> None:
    user = await world.ensure_granted_user(207)
    handoffs = FakeComposeHandoffs()
    token = await handoffs.store(ComposeHandoffPayload(user_id=user.id, draft="rl"))
    with pytest.raises(RateLimited):
        await _uc(world, handoffs=handoffs, rate_limiter=FakeRateLimiter(limit=0)).execute(
            RedeemComposeHandoffCommand(token)
        )


@pytest.mark.unit
async def test_redeem_unavailable_and_access(world: AppWorld) -> None:
    from svoi_pravila.application.use_cases.confirm_age import ConfirmAge, ConfirmAgeCommand
    from svoi_pravila.application.use_cases.ensure_user import EnsureUser, EnsureUserCommand
    from svoi_pravila.domain.ids import TelegramUserId

    handoffs = FakeComposeHandoffs()
    with pytest.raises(ComposeHandoffUnavailable):
        await _uc(world, handoffs=handoffs).execute(RedeemComposeHandoffCommand("h_" + ("z" * 64)))

    user = (
        await EnsureUser(world.uow_factory, world.ids, world.clock).execute(
            EnsureUserCommand(TelegramUserId(208))
        )
    ).user
    await ConfirmAge(world.uow_factory, world.clock).execute(ConfirmAgeCommand(user.id))
    token = await handoffs.store(ComposeHandoffPayload(user_id=user.id, draft="no-consent"))
    with pytest.raises(AccessNotGranted):
        await _uc(world, handoffs=handoffs).execute(RedeemComposeHandoffCommand(token))

    granted = await world.ensure_granted_user(209)
    gone_token = await handoffs.store(ComposeHandoffPayload(user_id=granted.id, draft="gone"))
    async with world.uow_factory() as uow:
        await uow.users.delete(granted.id)
        await uow.commit()
    with pytest.raises(ComposeHandoffUnavailable):
        await _uc(world, handoffs=handoffs).execute(RedeemComposeHandoffCommand(gone_token))
