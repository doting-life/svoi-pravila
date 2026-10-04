"""Leave a pair (D-7), keeping remaining-member authored text as contact-scope."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._leave_pair import dissolve_pair_for_leaving_member
from svoi_pravila.domain.ids import PairId, UserId


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
    """Dissolve a pair in one unit of work."""

    def __init__(self, uow_factory: UnitOfWorkFactory, ids: IdGenerator, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._ids = ids
        self._clock = clock

    async def execute(self, command: LeavePairCommand) -> LeavePairResult:
        """Rehome remaining-authored rules and delete the pair."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            pair = await uow.pairs.get(command.pair_id)
            if pair is None:
                raise NotFound()
            await dissolve_pair_for_leaving_member(
                uow,
                self._ids,
                actor_id=command.actor_id,
                pair=pair,
                now=now,
            )
            await uow.commit()
            return LeavePairResult(pair_id=command.pair_id)
