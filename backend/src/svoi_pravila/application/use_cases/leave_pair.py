"""Leave a pair and rehome shared rules for the remaining member (D-7)."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.pair_notifier import PairNotifier
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._leave_pair import dissolve_pair_for_leaving_member
from svoi_pravila.application.use_cases._pair_notify import notify_after_commit
from svoi_pravila.domain.ids import ContactId, PairId, UserId


@dataclass(frozen=True, slots=True)
class LeavePairCommand:
    """Input for LeavePair."""

    actor_id: UserId
    pair_id: PairId


@dataclass(frozen=True, slots=True)
class LeavePairResult:
    """Result of LeavePair."""

    pair_id: PairId


class LeavePair:
    """Dissolve a pair for the leaving member and notify the partner."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        notifier: PairNotifier,
    ) -> None:
        self._uow_factory = uow_factory
        self._ids = ids
        self._clock = clock
        self._notifier = notifier

    async def execute(self, command: LeavePairCommand) -> LeavePairResult:
        """Rehome remaining-authored rules and delete the pair; notify the partner."""
        remaining_user_id: UserId
        remaining_contact_id: ContactId
        now = self._clock.now()
        async with self._uow_factory() as uow:
            pair = await uow.pairs.get(command.pair_id)
            if pair is None:
                raise NotFound()
            if not pair.is_member(command.actor_id):
                raise NotFound()
            remaining_id = pair.other_member(command.actor_id)
            remaining_contact = await uow.contacts.get_for_owner_and_pair(remaining_id, pair.id)
            if remaining_contact is None:
                raise NotFound()
            remaining_user_id = remaining_id
            remaining_contact_id = remaining_contact.id
            await dissolve_pair_for_leaving_member(
                uow,
                self._ids,
                actor_id=command.actor_id,
                pair=pair,
                now=now,
            )
            await uow.commit()

        await notify_after_commit(
            lambda: self._notifier.partner_left(remaining_user_id, remaining_contact_id),
        )
        return LeavePairResult(pair_id=command.pair_id)
