"""Rule aggregate with revisions and dual-approval for pair scope."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from svoi_pravila.domain.enums import RuleCategory, RuleStatus
from svoi_pravila.domain.errors import InvalidTransitionError, InvalidValueError
from svoi_pravila.domain.ids import ContactId, PairId, RuleId, UserId
from svoi_pravila.domain.text import RuleText
from svoi_pravila.domain.time import require_utc

MAX_OPEN_RULES_PER_SCOPE = 50


@dataclass(frozen=True, slots=True)
class ContactScope:
    """Rule private to a single contact."""

    contact_id: ContactId


@dataclass(frozen=True, slots=True)
class PairScope:
    """Rule shared within a pair."""

    pair_id: PairId


RuleScope = ContactScope | PairScope


def _require(*, ok: bool, message: str) -> None:
    if not ok:
        raise InvalidValueError(message)


@dataclass(frozen=True, slots=True)
class RuleRevision:
    """One proposed or effective version of a rule's text."""

    number: int
    text: RuleText
    author_id: UserId
    proposed_at: datetime
    approved_by: frozenset[UserId]
    effective_since: datetime | None

    def __post_init__(self) -> None:
        if self.number < 1:
            msg = "revision number must be 1-based"
            raise InvalidValueError(msg)
        require_utc(self.proposed_at)
        if self.effective_since is not None:
            require_utc(self.effective_since)
            if self.effective_since < self.proposed_at:
                msg = "effective_since must be at or after proposed_at"
                raise InvalidValueError(msg)


@dataclass(frozen=True, slots=True)
class Rule:
    """Rule aggregate with ordered revisions and approval workflow."""

    id: RuleId
    scope: RuleScope
    category: RuleCategory
    approvers: frozenset[UserId]
    status: RuleStatus
    revisions: tuple[RuleRevision, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.created_at)
        _require(ok=bool(self.approvers), message="approvers must be non-empty")
        _require(ok=bool(self.revisions), message="rule must have at least one revision")
        numbers = [r.number for r in self.revisions]
        _require(
            ok=numbers == list(range(1, len(self.revisions) + 1)),
            message="revision numbers must be consecutive starting at 1",
        )
        self._validate_revisions()
        self._validate_status_shape()

    def _validate_revisions(self) -> None:
        pending_indexes: list[int] = []
        for index, revision in enumerate(self.revisions):
            _require(
                ok=bool(revision.approved_by),
                message="revision approved_by must be non-empty",
            )
            _require(
                ok=revision.approved_by <= self.approvers,
                message="revision approved_by must be a subset of approvers",
            )
            _require(
                ok=revision.author_id in revision.approved_by,
                message="revision approved_by must contain its author",
            )
            _require(
                ok=revision.proposed_at >= self.created_at,
                message="revision proposed_at must be at or after created_at",
            )
            _require(
                ok=revision.effective_since is None or revision.effective_since >= self.created_at,
                message="revision effective_since must be at or after created_at",
            )
            fully = revision.approved_by == self.approvers
            _require(
                ok=fully == (revision.effective_since is not None),
                message="effective_since must be set iff approved_by equals approvers",
            )
            if revision.effective_since is None:
                pending_indexes.append(index)
        _require(
            ok=len(pending_indexes) <= 1,
            message="at most one non-effective revision is allowed",
        )
        _require(
            ok=not pending_indexes or pending_indexes[0] == len(self.revisions) - 1,
            message="the only non-effective revision must be the last one",
        )

    def _validate_status_shape(self) -> None:
        single_pending = len(self.revisions) == 1 and self.revisions[0].effective_since is None
        if self.status in {RuleStatus.PROPOSED, RuleStatus.REJECTED}:
            _require(
                ok=single_pending,
                message=(f"{self.status.name} rules must have exactly one non-effective revision"),
            )
        elif self.status is RuleStatus.ACTIVE:
            _require(
                ok=self.effective_revision is not None,
                message="ACTIVE rules must have an effective revision",
            )

    @property
    def effective_revision(self) -> RuleRevision | None:
        """Latest revision with ``effective_since``, or None."""
        effective = [r for r in self.revisions if r.effective_since is not None]
        if not effective:
            return None
        return max(effective, key=lambda r: r.number)

    @property
    def pending_revision(self) -> RuleRevision | None:
        """Latest revision that is not yet effective, if any."""
        for revision in reversed(self.revisions):
            if revision.effective_since is None:
                return revision
        return None

    @classmethod
    def propose(
        cls,
        *,
        rule_id: RuleId,
        scope: RuleScope,
        category: RuleCategory,
        approvers: frozenset[UserId],
        author_id: UserId,
        text: RuleText,
        now: datetime,
    ) -> Rule:
        """Create a rule with the first revision."""
        require_utc(now)
        if not approvers:
            msg = "approvers must be non-empty"
            raise InvalidValueError(msg)
        if author_id not in approvers:
            msg = "author must be an approver"
            raise InvalidTransitionError(msg)
        approved_by = frozenset({author_id})
        fully_approved = approved_by == approvers
        revision = RuleRevision(
            number=1,
            text=text,
            author_id=author_id,
            proposed_at=now,
            approved_by=approved_by,
            effective_since=now if fully_approved else None,
        )
        status = RuleStatus.ACTIVE if fully_approved else RuleStatus.PROPOSED
        return cls(
            id=rule_id,
            scope=scope,
            category=category,
            approvers=approvers,
            status=status,
            revisions=(revision,),
            created_at=now,
        )

    @classmethod
    def rehome_authored_to_contact(
        cls,
        source: Rule,
        *,
        remaining_id: UserId,
        contact_id: ContactId,
        new_id: RuleId,
    ) -> Rule | None:
        """Copy remaining-member revisions onto a new private contact-scope rule.

        Revision numbers are re-based from 1. Approvers become ``{remaining_id}``.
        A sole remaining approver fully approves every kept revision, so a missing
        ``effective_since`` is filled with ``proposed_at``. ARCHIVED is preserved;
        other sources become ACTIVE. Returns ``None`` when the remaining member
        authored no revisions.
        """
        kept = [revision for revision in source.revisions if revision.author_id == remaining_id]
        if not kept:
            return None
        rebuilt = tuple(
            RuleRevision(
                number=index,
                text=revision.text,
                author_id=remaining_id,
                proposed_at=revision.proposed_at,
                approved_by=frozenset({remaining_id}),
                effective_since=revision.effective_since or revision.proposed_at,
            )
            for index, revision in enumerate(kept, start=1)
        )
        status = RuleStatus.ARCHIVED if source.status is RuleStatus.ARCHIVED else RuleStatus.ACTIVE
        return cls(
            id=new_id,
            scope=ContactScope(contact_id=contact_id),
            category=source.category,
            approvers=frozenset({remaining_id}),
            status=status,
            revisions=rebuilt,
            created_at=source.created_at,
        )

    def approve(self, user_id: UserId, now: datetime) -> Rule:
        """Approve the pending revision."""
        require_utc(now)
        if self.status not in {RuleStatus.PROPOSED, RuleStatus.ACTIVE}:
            msg = "can only approve on proposed or active rules"
            raise InvalidTransitionError(msg)
        if user_id not in self.approvers:
            msg = "user is not an approver"
            raise InvalidTransitionError(msg)
        pending = self.pending_revision
        if pending is None:
            msg = "no pending revision to approve"
            raise InvalidTransitionError(msg)
        if user_id in pending.approved_by:
            msg = "user already approved this revision"
            raise InvalidTransitionError(msg)
        new_approved = frozenset(pending.approved_by | {user_id})
        fully_approved = new_approved == self.approvers
        updated = replace(
            pending,
            approved_by=new_approved,
            effective_since=now if fully_approved else None,
        )
        new_revisions = tuple(updated if r.number == pending.number else r for r in self.revisions)
        new_status = self.status
        if fully_approved and self.status is RuleStatus.PROPOSED:
            new_status = RuleStatus.ACTIVE
        return replace(self, revisions=new_revisions, status=new_status)

    def propose_edit(self, author_id: UserId, text: RuleText, now: datetime) -> Rule:
        """Propose an edit while ACTIVE and with no pending revision."""
        require_utc(now)
        if self.status is not RuleStatus.ACTIVE:
            msg = "can only edit an active rule"
            raise InvalidTransitionError(msg)
        if self.pending_revision is not None:
            msg = "a pending revision already exists"
            raise InvalidTransitionError(msg)
        if author_id not in self.approvers:
            msg = "author must be an approver"
            raise InvalidTransitionError(msg)
        approved_by = frozenset({author_id})
        fully_approved = approved_by == self.approvers
        next_number = self.revisions[-1].number + 1
        revision = RuleRevision(
            number=next_number,
            text=text,
            author_id=author_id,
            proposed_at=now,
            approved_by=approved_by,
            effective_since=now if fully_approved else None,
        )
        return replace(self, revisions=(*self.revisions, revision))

    def reject_pending(self, user_id: UserId, now: datetime) -> Rule:
        """Reject the pending revision (approver other than its author)."""
        require_utc(now)
        if self.status not in {RuleStatus.PROPOSED, RuleStatus.ACTIVE}:
            msg = "can only reject pending revisions on proposed or active rules"
            raise InvalidTransitionError(msg)
        if user_id not in self.approvers:
            msg = "user is not an approver"
            raise InvalidTransitionError(msg)
        pending = self.pending_revision
        if pending is None:
            msg = "no pending revision to reject"
            raise InvalidTransitionError(msg)
        if user_id == pending.author_id:
            msg = "author cannot reject their own pending revision"
            raise InvalidTransitionError(msg)
        if pending.number == 1:
            return replace(self, status=RuleStatus.REJECTED)
        kept = tuple(r for r in self.revisions if r.number != pending.number)
        return replace(self, revisions=kept)

    def archive(self, user_id: UserId, now: datetime) -> Rule:
        """Archive an ACTIVE or PROPOSED rule."""
        require_utc(now)
        if user_id not in self.approvers:
            msg = "user is not an approver"
            raise InvalidTransitionError(msg)
        if self.status not in {RuleStatus.ACTIVE, RuleStatus.PROPOSED}:
            msg = "only active or proposed rules can be archived"
            raise InvalidTransitionError(msg)
        return replace(self, status=RuleStatus.ARCHIVED)
