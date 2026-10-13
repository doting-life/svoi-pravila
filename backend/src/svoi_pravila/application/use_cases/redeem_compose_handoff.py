"""Redeem a bearer compose handoff into a reusable compose session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from svoi_pravila.application.compose_handoff import ComposeHandoffPayload
from svoi_pravila.application.compose_session import (
    COMPOSE_SESSION_TTL_SECONDS,
    ComposeSessionPayload,
)
from svoi_pravila.application.errors import (
    ComposeHandoffUnavailable,
    RateLimited,
)
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.compose_handoffs import ComposeHandoffs
from svoi_pravila.application.ports.compose_sessions import ComposeSessions
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access


@dataclass(frozen=True, slots=True)
class RedeemComposeHandoffCommand:
    """Input for RedeemComposeHandoff (opaque ``h_`` token)."""

    token: str


@dataclass(frozen=True, slots=True)
class RedeemComposeHandoffResult:
    """Draft, session token and absolute expiry."""

    draft: str
    session_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RedeemComposeHandoffPorts:
    """Injected collaborators for RedeemComposeHandoff."""

    uow_factory: UnitOfWorkFactory
    catalog: ConsentCatalog
    compose_handoffs: ComposeHandoffs
    compose_sessions: ComposeSessions
    rate_limiter: RateLimiter
    pseudonymizer: Pseudonymizer
    clock: Clock


class RedeemComposeHandoff:
    """GETDEL handoff, authorize user, issue a 15-minute compose session."""

    def __init__(self, ports: RedeemComposeHandoffPorts) -> None:
        self._ports = ports

    async def execute(self, command: RedeemComposeHandoffCommand) -> RedeemComposeHandoffResult:
        """Redeem once; raise ComposeHandoffUnavailable / AccessNotGranted."""
        handoff: ComposeHandoffPayload = await self._ports.compose_handoffs.redeem_once(
            command.token
        )

        async with self._ports.uow_factory() as uow:
            user = await uow.users.get(handoff.user_id)
            if user is None:
                raise ComposeHandoffUnavailable()
            await require_access(uow, self._ports.catalog, user.id)

        pseudonym = self._ports.pseudonymizer.pseudonymize(
            "miniapp", str(user.telegram_user_id.value)
        )
        decision = await self._ports.rate_limiter.check(pseudonym)
        if not decision.allowed:
            raise RateLimited()

        session_token = await self._ports.compose_sessions.store(
            ComposeSessionPayload(user_id=user.id)
        )
        expires_at = self._ports.clock.now() + timedelta(seconds=COMPOSE_SESSION_TTL_SECONDS)
        return RedeemComposeHandoffResult(
            draft=handoff.draft,
            session_token=session_token,
            expires_at=expires_at,
        )
