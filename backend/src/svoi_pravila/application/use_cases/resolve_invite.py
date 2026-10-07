"""Resolve a raw invite token to an invite id without mutating state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import load_access_status, require_access
from svoi_pravila.domain.ids import InviteId, UserId
from svoi_pravila.domain.invite import InviteTokenHash


@dataclass(frozen=True, slots=True)
class ResolveInviteCommand:
    """Input for ResolveInvite."""

    actor_id: UserId
    raw_token: str


@dataclass(frozen=True, slots=True)
class ResolveInviteResult:
    """Resolved invite id and expiry; the raw token may be discarded by the caller."""

    invite_id: InviteId
    expires_at: datetime


class ResolveInvite:
    """Validate a raw token and return the invite id without storing the token."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._clock = clock

    async def execute(self, command: ResolveInviteCommand) -> ResolveInviteResult:
        """Hash the token, validate pending/expiry/self, return invite_id."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            token_hash = InviteTokenHash.from_raw_token(command.raw_token)
            invite = await uow.invites.get_by_token_hash(token_hash)
            if invite is None:
                raise NotFound()
            inviter_access = await load_access_status(uow, self._catalog, invite.inviter_id)
            if not inviter_access.granted:
                raise NotFound()
            invite.require_acceptable(command.actor_id, self._clock.now())
            return ResolveInviteResult(invite_id=invite.id, expires_at=invite.expires_at)
