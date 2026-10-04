"""D-7 dissolve: remaining member keeps authored pair revisions as contact-scope."""

from __future__ import annotations

from svoi_pravila.application.errors import NotFound, OpenRuleLimitReached
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.unit_of_work import UnitOfWork
from svoi_pravila.domain.enums import RuleStatus
from svoi_pravila.domain.ids import RuleId, UserId
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.rules import MAX_OPEN_RULES_PER_SCOPE, ContactScope, PairScope, Rule


async def dissolve_pair_for_leaving_member(
    uow: UnitOfWork,
    ids: IdGenerator,
    *,
    actor_id: UserId,
    pair: Pair,
) -> None:
    """Rehome remaining-authored pair rules, shred pair DEK, unlink, delete pair."""
    if not pair.is_member(actor_id):
        raise NotFound()
    remaining_id = pair.other_member(actor_id)
    remaining_contact = await uow.contacts.get_for_owner_and_pair(remaining_id, pair.id)
    if remaining_contact is None:
        raise NotFound()
    pair_rules = await uow.rules.list_for_scope(PairScope(pair_id=pair.id))
    copies: list[Rule] = []
    for source in pair_rules:
        copy = Rule.rehome_authored_to_contact(
            source,
            remaining_id=remaining_id,
            contact_id=remaining_contact.id,
            new_id=RuleId(ids.new_id()),
        )
        if copy is not None:
            copies.append(copy)
    open_copies = sum(1 for copy in copies if copy.status is RuleStatus.ACTIVE)
    existing_open = await uow.rules.count_open_for_scope(
        ContactScope(contact_id=remaining_contact.id)
    )
    if existing_open + open_copies > MAX_OPEN_RULES_PER_SCOPE:
        raise OpenRuleLimitReached()
    for copy in copies:
        await uow.rules.add(copy)
    for source in pair_rules:
        await uow.rules.delete(source.id)
    leaving_contact = await uow.contacts.get_for_owner_and_pair(actor_id, pair.id)
    await uow.contacts.update(remaining_contact.unlink_pair())
    if leaving_contact is not None:
        await uow.contacts.update(leaving_contact.unlink_pair())
    await uow.pairs.delete(pair.id)
