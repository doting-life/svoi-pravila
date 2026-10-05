"""Port for partner notifications about pair lifecycle events (C0/C1 only)."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.ids import ContactId, RuleId, UserId


class PairNotifier(Protocol):
    """Notify a user about pair events without revealing partner identity or labels."""

    async def invite_accepted(self, inviter_id: UserId, inviter_contact_id: ContactId) -> None:
        """Tell the inviter that their invite was accepted (uses their own contact label)."""
        ...

    async def shared_rule_proposed(self, approver_id: UserId, rule_id: RuleId) -> None:
        """Tell the partner that a shared rule awaits their decision."""
        ...

    async def shared_rule_decided(
        self, author_id: UserId, rule_id: RuleId, *, approved: bool
    ) -> None:
        """Tell the author of a shared-rule proposal about the decision."""
        ...

    async def partner_left(self, user_id: UserId, contact_id: ContactId) -> None:
        """Tell the remaining member that the shared rulebook with their contact ended."""
        ...
