"""Recording PairNotifier fake for unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from svoi_pravila.domain.ids import ContactId, RuleId, UserId


@dataclass
class RecordedInviteAccepted:
    """Captured invite_accepted call."""

    inviter_id: UserId
    inviter_contact_id: ContactId


@dataclass
class RecordedSharedRuleProposed:
    """Captured shared_rule_proposed call."""

    approver_id: UserId
    rule_id: RuleId


@dataclass
class RecordedSharedRuleDecided:
    """Captured shared_rule_decided call."""

    author_id: UserId
    rule_id: RuleId
    approved: bool


@dataclass
class RecordedPartnerLeft:
    """Captured partner_left call."""

    user_id: UserId
    contact_id: ContactId


@dataclass
class FakePairNotifier:
    """In-memory PairNotifier; optionally raises after recording."""

    fail: bool = False
    invite_accepted_calls: list[RecordedInviteAccepted] = field(default_factory=list)
    shared_rule_proposed_calls: list[RecordedSharedRuleProposed] = field(default_factory=list)
    shared_rule_decided_calls: list[RecordedSharedRuleDecided] = field(default_factory=list)
    partner_left_calls: list[RecordedPartnerLeft] = field(default_factory=list)

    async def invite_accepted(self, inviter_id: UserId, inviter_contact_id: ContactId) -> None:
        """Record invite acceptance; optionally raise."""
        self.invite_accepted_calls.append(RecordedInviteAccepted(inviter_id, inviter_contact_id))
        if self.fail:
            msg = "notifier failure"
            raise RuntimeError(msg)

    async def shared_rule_proposed(self, approver_id: UserId, rule_id: RuleId) -> None:
        """Record shared proposal; optionally raise."""
        self.shared_rule_proposed_calls.append(RecordedSharedRuleProposed(approver_id, rule_id))
        if self.fail:
            msg = "notifier failure"
            raise RuntimeError(msg)

    async def shared_rule_decided(
        self, author_id: UserId, rule_id: RuleId, *, approved: bool
    ) -> None:
        """Record shared decision; optionally raise."""
        self.shared_rule_decided_calls.append(
            RecordedSharedRuleDecided(author_id, rule_id, approved)
        )
        if self.fail:
            msg = "notifier failure"
            raise RuntimeError(msg)

    async def partner_left(self, user_id: UserId, contact_id: ContactId) -> None:
        """Record partner left; optionally raise."""
        self.partner_left_calls.append(RecordedPartnerLeft(user_id, contact_id))
        if self.fail:
            msg = "notifier failure"
            raise RuntimeError(msg)
