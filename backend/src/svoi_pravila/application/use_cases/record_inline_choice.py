"""Record D-9 inline choice events from ``chosen_inline_result``."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import UsageEventWriteFailed
from svoi_pravila.application.inline_result_ref import parse_inline_result_ref
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.usage_event_sink import UsageEventSink
from svoi_pravila.domain.enums import (
    UsageEventKind,
    UsageOutcome,
    UsageSurface,
)
from svoi_pravila.domain.ids import TelegramUserId, UsageEventId
from svoi_pravila.domain.usage import UsageEvent

_ANALYTICS_PURPOSE = "analytics"


@dataclass(frozen=True, slots=True)
class RecordInlineChoiceCommand:
    """Input for RecordInlineChoice. Never includes query text."""

    telegram_user_id: TelegramUserId
    result_ref: str


class RecordInlineChoice:
    """Persist a C0 ``result_chosen`` event for an inline article selection."""

    def __init__(
        self,
        sink: UsageEventSink,
        clock: Clock,
        ids: IdGenerator,
        pseudonymizer: Pseudonymizer,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._ids = ids
        self._pseudonymizer = pseudonymizer

    async def execute(self, command: RecordInlineChoiceCommand) -> None:
        """Parse ``result_ref`` and record the choice. Query text is not accepted."""
        scenario, firmness = parse_inline_result_ref(command.result_ref)
        analytics = self._pseudonymizer.pseudonymize(
            _ANALYTICS_PURPOSE, str(command.telegram_user_id.value)
        )
        event = UsageEvent(
            id=UsageEventId(self._ids.new_id()),
            occurred_at=self._clock.now().replace(microsecond=0),
            user_pseudonym=analytics,
            scenario=scenario,
            surface=UsageSurface.INLINE,
            outcome=UsageOutcome.OK,
            unavailable_kind=None,
            safety=None,
            model=None,
            prompt_version=None,
            latency_ms=0,
            ttfc_ms=None,
            attempts=0,
            input_tokens=0,
            output_tokens=0,
            billable_tokens=0,
            event_kind=UsageEventKind.RESULT_CHOSEN,
            variant_firmness=firmness,
        )
        try:
            await self._sink.record(event)
        except UsageEventWriteFailed:
            return
