"""Shared LLM context: contact relationship and effective rules."""

from __future__ import annotations

from svoi_pravila.application.ports.generation import RuleContext
from svoi_pravila.application.ports.unit_of_work import UnitOfWork
from svoi_pravila.application.use_cases._contact_access import load_owned_contact
from svoi_pravila.application.use_cases._effective_rules import collect_effective_rules
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.ids import ContactId
from svoi_pravila.domain.user import User


async def load_contact_rule_context(
    uow: UnitOfWork,
    user: User,
    contact_id: ContactId | None,
) -> tuple[RelationshipKind, tuple[RuleContext, ...]]:
    """Return OTHER/no rules when ``contact_id`` is None; else owned contact context."""
    if contact_id is None:
        return RelationshipKind.OTHER, ()
    contact = await uow.contacts.get(contact_id)
    contact, pair = await load_owned_contact(uow, user.id, contact)
    views = await collect_effective_rules(uow, contact, pair)
    return contact.relationship, tuple(
        RuleContext(
            category=view.category,
            text=view.text.value,
            effective_since=view.effective_since,
        )
        for view in views
    )


async def load_active_contact_rule_context(
    uow: UnitOfWork,
    user: User,
) -> tuple[RelationshipKind, tuple[RuleContext, ...]]:
    """Return OTHER/no rules when no active contact, else owned contact context."""
    return await load_contact_rule_context(uow, user, user.active_contact_id)
