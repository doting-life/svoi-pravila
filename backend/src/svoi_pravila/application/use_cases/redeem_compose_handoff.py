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
    NotFound,
    RateLimited,
)
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.compose_handoffs import ComposeHandoffs
from svoi_pravila.application.ports.compose_sessions import ComposeSessions
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.ports.telegram_public_username import TelegramPublicUsername
from svoi_pravila.application.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._contact_access import load_owned_contact
from svoi_pravila.domain.ids import ContactId, TelegramUserId, UserId


@dataclass(frozen=True, slots=True)
class RedeemComposeHandoffCommand:
    """Input for RedeemComposeHandoff (opaque ``h_`` token)."""

    token: str


@dataclass(frozen=True, slots=True)
class ComposePartnerContext:
    """Partner shown on the inline compose screen (label never logged)."""

    contact_id: ContactId
    label: str
    username: str | None


@dataclass(frozen=True, slots=True)
class RedeemComposeHandoffResult:
    """Draft, optional partner, session token and absolute expiry."""

    draft: str
    partner: ComposePartnerContext | None
    session_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RedeemComposeHandoffPorts:
    """Injected collaborators for RedeemComposeHandoff."""

    uow_factory: UnitOfWorkFactory
    catalog: ConsentCatalog
    compose_handoffs: ComposeHandoffs
    compose_sessions: ComposeSessions
    usernames: TelegramPublicUsername
    rate_limiter: RateLimiter
    pseudonymizer: Pseudonymizer
    clock: Clock


@dataclass(frozen=True, slots=True)
class _PartnerLookup:
    """Owned paired contact plus optional partner Telegram id (for getChat after UoW)."""

    contact_id: ContactId
    label: str
    partner_telegram_id: TelegramUserId | None


class RedeemComposeHandoff:
    """GETDEL handoff, authorize user, issue a 15-minute compose session."""

    def __init__(self, ports: RedeemComposeHandoffPorts) -> None:
        self._ports = ports

    async def execute(self, command: RedeemComposeHandoffCommand) -> RedeemComposeHandoffResult:
        """Redeem once; raise ComposeHandoffUnavailable / AccessNotGranted / NotFound."""
        handoff: ComposeHandoffPayload = await self._ports.compose_handoffs.redeem_once(
            command.token
        )

        async with self._ports.uow_factory() as uow:
            user = await uow.users.get(handoff.user_id)
            if user is None:
                raise ComposeHandoffUnavailable()
            await require_access(uow, self._ports.catalog, user.id)
            partner_lookup = await _resolve_partner_in_uow(uow, user.id, handoff.contact_id)

        pseudonym = self._ports.pseudonymizer.pseudonymize(
            "miniapp", str(user.telegram_user_id.value)
        )
        decision = await self._ports.rate_limiter.check(pseudonym)
        if not decision.allowed:
            raise RateLimited()

        partner: ComposePartnerContext | None = None
        if partner_lookup is not None:
            username: str | None = None
            if partner_lookup.partner_telegram_id is not None:
                username = await self._ports.usernames.username_for(
                    partner_lookup.partner_telegram_id
                )
            partner = ComposePartnerContext(
                contact_id=partner_lookup.contact_id,
                label=partner_lookup.label,
                username=username,
            )

        session_token = await self._ports.compose_sessions.store(
            ComposeSessionPayload(user_id=user.id, contact_id=handoff.contact_id)
        )
        expires_at = self._ports.clock.now() + timedelta(seconds=COMPOSE_SESSION_TTL_SECONDS)
        return RedeemComposeHandoffResult(
            draft=handoff.draft,
            partner=partner,
            session_token=session_token,
            expires_at=expires_at,
        )


async def _resolve_partner_in_uow(
    uow: UnitOfWork,
    actor_id: UserId,
    contact_id: ContactId | None,
) -> _PartnerLookup | None:
    if contact_id is None:
        return None
    try:
        contact = await uow.contacts.get(contact_id)
        contact, pair = await load_owned_contact(uow, actor_id, contact)
    except NotFound:
        return None
    if pair is None:
        return None
    label = contact.label.value
    partner_id = pair.other_member(actor_id)
    partner_user = await uow.users.get(partner_id)
    telegram_id = None if partner_user is None else partner_user.telegram_user_id
    return _PartnerLookup(contact_id=contact_id, label=label, partner_telegram_id=telegram_id)
