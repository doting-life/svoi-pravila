"""Record D-9 inline choice events and append tone signals (D-6)."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from svoi_pravila.application.errors import (
    AccessNotGranted,
    ConflictError,
    UsageEventWriteFailed,
)
from svoi_pravila.application.inline_result_ref import parse_inline_result_ref
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.tone_suggestion_catalog import ToneSuggestionCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.ports.usage_event_sink import UsageEventSink
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.domain.enums import (
    Firmness,
    RuleCategory,
    UsageEventKind,
    UsageOutcome,
    UsageSurface,
)
from svoi_pravila.domain.ids import RuleSuggestionId, TelegramUserId, UsageEventId
from svoi_pravila.domain.rule_suggestion import (
    RuleSuggestion,
    ToneSignal,
    dominant_firmness,
)
from svoi_pravila.domain.usage import UsageEvent

_ANALYTICS_PURPOSE = "analytics"


@dataclass(frozen=True, slots=True)
class RecordInlineChoiceCommand:
    """Input for RecordInlineChoice. Never includes query text."""

    telegram_user_id: TelegramUserId
    result_ref: str


@dataclass(frozen=True, slots=True)
class RecordInlineChoiceResult:
    """Outcome of recording a choice; suggestion id when a new tone candidate was created."""

    suggestion_id: RuleSuggestionId | None


@dataclass(frozen=True, slots=True)
class RecordInlineChoicePorts:
    """Collaborators for RecordInlineChoice."""

    sink: UsageEventSink
    uow_factory: UnitOfWorkFactory
    catalog: ConsentCatalog
    tone_catalog: ToneSuggestionCatalog
    clock: Clock
    ids: IdGenerator
    pseudonymizer: Pseudonymizer


class RecordInlineChoice:
    """Persist a C0 ``result_chosen`` event and optionally create a tone suggestion.

    The firmness is attributed to the user's **active contact at choice time**.
    Usage-event recording (txn 1 via ``UsageEventSink``) and the tone-signal path
    (txn 2 via its own unit of work) are independent: failure of either does not
    roll back the other, and signal-path failures never break choice recording.
    """

    def __init__(self, ports: RecordInlineChoicePorts) -> None:
        self._ports = ports

    async def execute(self, command: RecordInlineChoiceCommand) -> RecordInlineChoiceResult:
        """Parse ``result_ref``, record the choice, then try the tone-signal path."""
        scenario, firmness = parse_inline_result_ref(command.result_ref)
        analytics = self._ports.pseudonymizer.pseudonymize(
            _ANALYTICS_PURPOSE, str(command.telegram_user_id.value)
        )
        event = UsageEvent(
            id=UsageEventId(self._ports.ids.new_id()),
            occurred_at=self._ports.clock.now().replace(microsecond=0),
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
        with contextlib.suppress(UsageEventWriteFailed):
            await self._ports.sink.record(event)

        suggestion_id = await self._try_tone_signal(command, firmness)
        return RecordInlineChoiceResult(suggestion_id=suggestion_id)

    async def _try_tone_signal(
        self,
        command: RecordInlineChoiceCommand,
        firmness: Firmness,
    ) -> RuleSuggestionId | None:
        try:
            return await self._append_tone_signal(command, firmness)
        except (ConflictError, OSError, TimeoutError, RuntimeError):
            return None

    async def _append_tone_signal(
        self,
        command: RecordInlineChoiceCommand,
        firmness: Firmness,
    ) -> RuleSuggestionId | None:
        async with self._ports.uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if user is None or user.active_contact_id is None:
                return None
            try:
                await require_access(uow, self._ports.catalog, user.id)
            except AccessNotGranted:
                return None
            contact_id = user.active_contact_id
            contact = await uow.contacts.get(contact_id)
            if contact is None or contact.owner_id != user.id:
                return None

            existing = await uow.tone_signals.get(user.id, contact_id)
            signal = existing or ToneSignal(
                user_id=user.id,
                contact_id=contact_id,
                values=(),
            )
            signal = signal.append(firmness)
            await uow.tone_signals.upsert(signal)

            created_id: RuleSuggestionId | None = None
            dominant = dominant_firmness(signal)
            if dominant is not None:
                prior = await uow.rule_suggestions.get_tone(user.id, contact_id, dominant)
                if prior is None:
                    suggestion = RuleSuggestion.create_tone(
                        suggestion_id=RuleSuggestionId(self._ports.ids.new_id()),
                        user_id=user.id,
                        contact_id=contact_id,
                        category=RuleCategory.HOW_TO_ASK,
                        text=self._ports.tone_catalog.template(dominant),
                        firmness=dominant,
                        now=self._ports.clock.now(),
                    )
                    await uow.rule_suggestions.add(suggestion)
                    created_id = suggestion.id
            await uow.commit()
            return created_id
