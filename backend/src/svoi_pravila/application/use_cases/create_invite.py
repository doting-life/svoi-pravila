"""Create an invite for an unlinked contact."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import ContactAlreadyLinked, NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.token_generator import TokenGenerator
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.domain.ids import ContactId, InviteId, UserId
from svoi_pravila.domain.invite import Invite, InviteTokenHash


@dataclass(frozen=True, slots=True)
class CreateInviteCommand:
    """Input for CreateInvite."""

    actor_id: UserId
    contact_id: ContactId


@dataclass(frozen=True, slots=True)
class CreateInviteResult:
    """Result of CreateInvite; raw token returned exactly once."""

    invite: Invite
    raw_token: str


class CreateInvite:
    """Create an invite; store only the token hash."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
        ids: IdGenerator,
        tokens: TokenGenerator,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._ids = ids
        self._tokens = tokens
        self._clock = clock

    async def execute(self, command: CreateInviteCommand) -> CreateInviteResult:
        """Create invite for an owned, unlinked contact."""
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            contact = await uow.contacts.get(command.contact_id)
            if contact is None or contact.owner_id != command.actor_id:
                raise NotFound()
            if contact.pair_id is not None:
                raise ContactAlreadyLinked()
            raw_token = self._tokens.new_invite_token()
            invite = Invite.create(
                invite_id=InviteId(self._ids.new_id()),
                inviter_id=command.actor_id,
                contact_id=contact.id,
                token_hash=InviteTokenHash.from_raw_token(raw_token),
                created_at=self._clock.now(),
            )
            await uow.invites.add(invite)
            await uow.commit()
            return CreateInviteResult(invite=invite, raw_token=raw_token)
