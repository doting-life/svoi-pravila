"""Revoke every unrevoked consent for a user."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.ids import TelegramUserId


@dataclass(frozen=True, slots=True)
class RevokeAllConsentsCommand:
    """Input for RevokeAllConsents."""

    telegram_user_id: TelegramUserId


@dataclass(frozen=True, slots=True)
class RevokeAllConsentsResult:
    """Result of RevokeAllConsents. ``found`` is False when the user is unknown."""

    found: bool
    consents: tuple[Consent, ...]


class RevokeAllConsents:
    """Revoke all unrevoked consents in one unit of work; idempotent."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: RevokeAllConsentsCommand) -> RevokeAllConsentsResult:
        """Revoke every live consent; no-data when the Telegram user is unknown."""
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if user is None:
                return RevokeAllConsentsResult(found=False, consents=())
            now = self._clock.now()
            revoked: list[Consent] = []
            for consent in await uow.consents.list_for_user(user.id):
                if consent.revoked_at is None:
                    updated = consent.revoke(now)
                    await uow.consents.update(updated)
                    revoked.append(updated)
            if revoked:
                await uow.commit()
            return RevokeAllConsentsResult(found=True, consents=tuple(revoked))
