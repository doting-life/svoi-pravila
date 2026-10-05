"""Shared private/shared rule proposal inside an open unit of work."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import NotFound, OpenRuleLimitReached
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.unit_of_work import UnitOfWork
from svoi_pravila.domain.enums import RuleCategory
from svoi_pravila.domain.ids import ContactId, RuleId, UserId
from svoi_pravila.domain.rules import (
    MAX_OPEN_RULES_PER_SCOPE,
    ContactScope,
    PairScope,
    Rule,
)
from svoi_pravila.domain.text import RuleText


@dataclass(frozen=True, slots=True)
class ProposeRuleParams:
    """Parameters for proposing a rule inside an open unit of work."""

    actor_id: UserId
    contact_id: ContactId
    category: RuleCategory
    text: RuleText
    shared: bool


async def propose_rule_in_uow(
    uow: UnitOfWork,
    *,
    ids: IdGenerator,
    clock: Clock,
    params: ProposeRuleParams,
) -> Rule:
    """Create a rule in ``uow`` without committing. Raises typed application errors."""
    contact = await uow.contacts.get(params.contact_id)
    if contact is None:
        raise NotFound()

    if params.shared:
        if contact.pair_id is None:
            raise NotFound()
        pair = await uow.pairs.get(contact.pair_id)
        if pair is None or not pair.is_member(params.actor_id):
            raise NotFound()
        scope: ContactScope | PairScope = PairScope(pair_id=pair.id)
        approvers = frozenset(pair.members)
    else:
        if contact.owner_id != params.actor_id:
            raise NotFound()
        scope = ContactScope(contact_id=contact.id)
        approvers = frozenset({params.actor_id})

    open_count = await uow.rules.count_open_for_scope(scope)
    if open_count >= MAX_OPEN_RULES_PER_SCOPE:
        raise OpenRuleLimitReached()

    rule = Rule.propose(
        rule_id=RuleId(ids.new_id()),
        scope=scope,
        category=params.category,
        approvers=approvers,
        author_id=params.actor_id,
        text=params.text,
        now=clock.now(),
    )
    await uow.rules.add(rule)
    return rule
