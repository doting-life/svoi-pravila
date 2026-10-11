"""Return a paired partner's public Telegram username (request-scoped; never stored)."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.telegram_public_username import TelegramPublicUsername
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._contact_access import load_owned_contact
from svoi_pravila.domain.ids import ContactId, UserId


@dataclass(frozen=True, slots=True)
class GetPartnerUsernameCommand:
    """Input for GetPartnerUsername."""

    actor_id: UserId
    contact_id: ContactId


@dataclass(frozen=True, slots=True)
class GetPartnerUsernameResult:
    """Public username without ``@``, or ``None`` when unavailable."""

    username: str | None


@dataclass(frozen=True, slots=True)
class GetPartnerUsernamePorts:
    """Injected collaborators for GetPartnerUsername."""

    uow_factory: UnitOfWorkFactory
    catalog: ConsentCatalog
    usernames: TelegramPublicUsername


class GetPartnerUsername:
    """Resolve the pair partner's Telegram username via live ``getChat``."""

    def __init__(self, ports: GetPartnerUsernamePorts) -> None:
        self._ports = ports

    async def execute(self, command: GetPartnerUsernameCommand) -> GetPartnerUsernameResult:
        """Authorize ownership; return ``None`` when unpaired or username missing."""
        async with self._ports.uow_factory() as uow:
            await require_access(uow, self._ports.catalog, command.actor_id)
            contact = await uow.contacts.get(command.contact_id)
            contact, pair = await load_owned_contact(uow, command.actor_id, contact)
            if pair is None:
                return GetPartnerUsernameResult(username=None)
            partner_id = pair.other_member(command.actor_id)
            partner = await uow.users.get(partner_id)
            if partner is None:
                raise NotFound()
            telegram_user_id = partner.telegram_user_id
        username = await self._ports.usernames.username_for(telegram_user_id)
        return GetPartnerUsernameResult(username=username)
