"""Revoke consents of a given kind."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.ids import UserId


@dataclass(frozen=True, slots=True)
class RevokeConsentCommand:
    """Input for RevokeConsent."""

    user_id: UserId
    kind: ConsentKind


@dataclass(frozen=True, slots=True)
class RevokeConsentResult:
    """Result of RevokeConsent."""

    consents: tuple[Consent, ...]


class RevokeConsent:
    """Revoke every unrevoked consent of the given kind."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: RevokeConsentCommand) -> RevokeConsentResult:
        """Revoke matching consents; empty tuple when nothing to revoke."""
        async with self._uow_factory() as uow:
            user = await uow.users.get(command.user_id)
            if user is None:
                raise NotFound()
            consents = await uow.consents.list_for_user(command.user_id)
            now = self._clock.now()
            revoked: list[Consent] = []
            for consent in consents:
                if consent.kind is command.kind and consent.revoked_at is None:
                    updated = consent.revoke(now)
                    await uow.consents.update(updated)
                    revoked.append(updated)
            if revoked:
                await uow.commit()
            return RevokeConsentResult(consents=tuple(revoked))
