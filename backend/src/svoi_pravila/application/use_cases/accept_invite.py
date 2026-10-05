"""Accept an invite and form a pair."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import AlreadyPaired, ContactLimitReached, NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.pair_notifier import PairNotifier
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import load_access_status, require_access
from svoi_pravila.application.use_cases._pair_notify import notify_after_commit
from svoi_pravila.domain.contact import MAX_CONTACTS_PER_USER, Contact
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.ids import ContactId, InviteId, PairId, UserId
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.text import ContactLabel


@dataclass(frozen=True, slots=True)
class AcceptInviteCommand:
    """Input for AcceptInvite (invite already resolved; raw token not accepted)."""

    actor_id: UserId
    invite_id: InviteId
    label_for_inviter: ContactLabel
    relationship: RelationshipKind


@dataclass(frozen=True, slots=True)
class AcceptInviteResult:
    """Result of AcceptInvite (invitee-visible data only)."""

    pair: Pair
    invitee_contact: Contact


class AcceptInvite:
    """Accept invite: create pair, link contacts, mark invite accepted."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
        ids: IdGenerator,
        clock: Clock,
        notifier: PairNotifier,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._ids = ids
        self._clock = clock
        self._notifier = notifier

    async def execute(self, command: AcceptInviteCommand) -> AcceptInviteResult:
        """Re-validate invite and form the pair in one unit of work."""
        inviter_id: UserId
        inviter_contact_id: ContactId
        async with self._uow_factory() as uow:
            await require_access(uow, self._catalog, command.actor_id)
            invite = await uow.invites.get(command.invite_id)
            if invite is None:
                raise NotFound()

            inviter_access = await load_access_status(uow, self._catalog, invite.inviter_id)
            if not inviter_access.granted:
                raise NotFound()

            invitee_count = await uow.contacts.count_for_owner(command.actor_id)
            if invitee_count >= MAX_CONTACTS_PER_USER:
                raise ContactLimitReached()

            now = self._clock.now()
            accepted = invite.accept(command.actor_id, now)

            existing_pair = await uow.pairs.find_between(invite.inviter_id, command.actor_id)
            if existing_pair is not None:
                raise AlreadyPaired()

            inviter_contact = await uow.contacts.get(invite.contact_id)
            if inviter_contact is None or inviter_contact.owner_id != invite.inviter_id:
                raise NotFound()
            if inviter_contact.pair_id is not None:
                raise AlreadyPaired()

            pair = Pair(
                id=PairId(self._ids.new_id()),
                members=frozenset({invite.inviter_id, command.actor_id}),
                created_at=now,
            )
            linked_inviter = inviter_contact.link_pair(pair.id)
            invitee_contact = Contact(
                id=ContactId(self._ids.new_id()),
                owner_id=command.actor_id,
                label=command.label_for_inviter,
                relationship=command.relationship,
                pair_id=pair.id,
                created_at=now,
            )

            await uow.pairs.add(pair)
            await uow.contacts.update(linked_inviter)
            await uow.contacts.add(invitee_contact)
            await uow.invites.update(accepted)
            invitee = await uow.users.get(command.actor_id)
            if invitee is None:
                raise NotFound()
            if invitee.active_contact_id is None:
                await uow.users.update(invitee.set_active_contact(invitee_contact.id))
            inviter_id = invite.inviter_id
            inviter_contact_id = invite.contact_id
            await uow.commit()

        await notify_after_commit(
            lambda: self._notifier.invite_accepted(inviter_id, inviter_contact_id),
        )
        return AcceptInviteResult(
            pair=pair,
            invitee_contact=invitee_contact,
        )
