"""Helpers for contact ownership without existence leaks."""

from __future__ import annotations

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.unit_of_work import UnitOfWork
from svoi_pravila.domain.contact import Contact
from svoi_pravila.domain.ids import RuleId, UserId
from svoi_pravila.domain.pair import Pair
from svoi_pravila.domain.rules import Rule, RuleRevision


def require_pending_revision(rule: Rule) -> RuleRevision:
    """Return the pending revision or NotFound when none exists."""
    pending = rule.pending_revision
    if pending is None:
        raise NotFound()
    return pending


async def load_owned_contact(
    uow: UnitOfWork,
    actor_id: UserId,
    contact: Contact | None,
) -> tuple[Contact, Pair | None]:
    """Return contact if owned by actor; else NotFound."""
    if contact is None or contact.owner_id != actor_id:
        raise NotFound()
    pair = None
    if contact.pair_id is not None:
        pair = await uow.pairs.get(contact.pair_id)
    return contact, pair


async def load_rule_for_approver(
    uow: UnitOfWork,
    actor_id: UserId,
    rule_id: RuleId,
) -> Rule:
    """Return rule if actor is an approver; missing/unauthorized → NotFound."""
    rule = await uow.rules.get(rule_id)
    if rule is None or actor_id not in rule.approvers:
        raise NotFound()
    return rule
