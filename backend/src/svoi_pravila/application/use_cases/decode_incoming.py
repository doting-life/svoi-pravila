"""Decode an incoming private-chat message with streaming generation."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    IncomingTextTooShort,
    InvalidGenerationOutput,
    NotFound,
    ScenarioBusy,
    ScenarioQuotaExceeded,
    UsageEventWriteFailed,
)
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.concurrency import ConcurrencyGuard
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.generation import (
    BOUNDED_TEXT_MAX,
    BOUNDED_TEXT_MIN,
    AnalysisChunk,
    DecodeCompleted,
    DecodeEvent,
    DecodeRequest,
    DecodeResult,
    GenerationMeta,
    RuleContext,
    SafetyVerdict,
    TextGenerator,
    TokenUsage,
)
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.monotonic import MonotonicClock
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.ports.usage_event_sink import UsageEventSink
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._generation_context import load_active_contact_rule_context
from svoi_pravila.domain.enums import (
    RelationshipKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import TelegramUserId, UsageEventId
from svoi_pravila.domain.usage import UsageEvent

_LOCK_MARGIN_SECONDS = 5
_RATE_LIMIT_PURPOSE = "rate_limit"
_QUOTA_PURPOSE = "decode_quota"
_ANALYTICS_PURPOSE = "analytics"


@dataclass(frozen=True, slots=True)
class DecodeIncomingCommand:
    """Input for DecodeIncoming."""

    telegram_user_id: TelegramUserId
    incoming_text: str


@dataclass(frozen=True, slots=True)
class DecodeIncomingPorts:
    """Injected collaborators for DecodeIncoming."""

    uow_factory: UnitOfWorkFactory
    catalog: ConsentCatalog
    generator: TextGenerator
    guard: ConcurrencyGuard
    quota: RateLimiter
    sink: UsageEventSink
    clock: Clock
    monotonic: MonotonicClock
    ids: IdGenerator
    pseudonymizer: Pseudonymizer
    crisis_screen: CrisisScreen
    deadline_seconds: float


class IncomingDecoder(Protocol):
    """Streaming decode use case as seen by the channel adapter."""

    def execute(self, command: DecodeIncomingCommand) -> AsyncIterator[DecodeEvent]:
        """Yield analysis chunks then a completed result, or raise a typed error."""
        ...


@dataclass(frozen=True, slots=True)
class _UsageDraft:
    """C0 fields for a usage event before timestamps are filled in."""

    user_key: str
    started: float
    first_chunk_at: float | None
    outcome: UsageOutcome
    model: str | None
    prompt_version: str | None
    latency_ms: int
    attempts: int
    usage: TokenUsage
    unavailable_kind: str | None
    safety: str | None


class DecodeIncoming:
    """Stream a decode for a fully onboarded user."""

    def __init__(self, ports: DecodeIncomingPorts) -> None:
        self._ports = ports

    async def execute(self, command: DecodeIncomingCommand) -> AsyncGenerator[DecodeEvent]:
        """Yield analysis chunks then a completed result, or raise a typed error."""
        text = command.incoming_text
        if len(text) < BOUNDED_TEXT_MIN:
            raise IncomingTextTooShort()
        if len(text) > BOUNDED_TEXT_MAX:
            raise IncomingTextTooLong()

        relationship, rules = await self._load_context(command.telegram_user_id)
        user_key = str(command.telegram_user_id.value)
        if self._ports.crisis_screen.hit(text):
            completed = _screened_decode_completed()
            await self._persist(self._event_from_screened(user_key))
            yield completed
            return
        lock_pseudonym = self._ports.pseudonymizer.pseudonymize(_RATE_LIMIT_PURPOSE, user_key)
        lock_key = f"tg:decode:lock:{lock_pseudonym}"
        ttl = int(self._ports.deadline_seconds) + _LOCK_MARGIN_SECONDS
        token = await self._ports.guard.acquire(lock_key, ttl_seconds=ttl)
        if token is None:
            raise ScenarioBusy()
        started = self._ports.monotonic.monotonic()
        first_chunk_at: float | None = None
        try:
            quota_pseudonym = self._ports.pseudonymizer.pseudonymize(_QUOTA_PURPOSE, user_key)
            decision = await self._ports.quota.check(quota_pseudonym)
            if not decision.allowed:
                raise ScenarioQuotaExceeded()
            request = DecodeRequest(
                incoming=text,
                rules=rules,
                relationship=relationship,
                deadline_seconds=self._ports.deadline_seconds,
            )
            async for event in self._ports.generator.decode_stream(request):
                if isinstance(event, AnalysisChunk):
                    if first_chunk_at is None:
                        first_chunk_at = self._ports.monotonic.monotonic()
                    yield event
                else:
                    yield event
                    await self._persist(
                        self._event_from_completed(
                            user_key=user_key,
                            started=started,
                            first_chunk_at=first_chunk_at,
                            completed=event,
                        )
                    )
        except (
            GenerationUnavailable,
            GenerationRefusedByProvider,
            InvalidGenerationOutput,
        ) as exc:
            await self._persist(
                self._event_from_failure(
                    user_key=user_key,
                    started=started,
                    first_chunk_at=first_chunk_at,
                    error=exc,
                )
            )
            raise
        finally:
            await self._ports.guard.release(lock_key, token)

    async def _load_context(
        self, telegram_user_id: TelegramUserId
    ) -> tuple[RelationshipKind, tuple[RuleContext, ...]]:
        async with self._ports.uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                raise NotFound()
            await require_access(uow, self._ports.catalog, user.id)
            return await load_active_contact_rule_context(uow, user)

    def _base_event(self, draft: _UsageDraft) -> UsageEvent:
        ended = self._ports.monotonic.monotonic()
        measured = max(0, int((ended - draft.started) * 1000))
        ttfc_ms = (
            None
            if draft.first_chunk_at is None
            else max(0, int((draft.first_chunk_at - draft.started) * 1000))
        )
        occurred = self._ports.clock.now().replace(microsecond=0)
        analytics = self._ports.pseudonymizer.pseudonymize(_ANALYTICS_PURPOSE, draft.user_key)
        return UsageEvent(
            id=UsageEventId(self._ports.ids.new_id()),
            occurred_at=occurred,
            user_pseudonym=analytics,
            scenario=UsageScenario.DECODE,
            surface=UsageSurface.DM,
            outcome=draft.outcome,
            unavailable_kind=draft.unavailable_kind,
            safety=draft.safety,
            model=draft.model,
            prompt_version=draft.prompt_version,
            latency_ms=(
                draft.latency_ms
                if draft.outcome in {UsageOutcome.OK, UsageOutcome.SCREENED}
                else measured
            ),
            ttfc_ms=ttfc_ms,
            attempts=draft.attempts,
            input_tokens=draft.usage.input,
            output_tokens=draft.usage.output,
            billable_tokens=draft.usage.billable,
        )

    def _event_from_screened(self, user_key: str) -> UsageEvent:
        return self._base_event(
            _UsageDraft(
                user_key=user_key,
                started=self._ports.monotonic.monotonic(),
                first_chunk_at=None,
                outcome=UsageOutcome.SCREENED,
                model=None,
                prompt_version=None,
                latency_ms=0,
                attempts=0,
                usage=TokenUsage(),
                unavailable_kind=None,
                safety=SafetyVerdict.CRISIS.value,
            )
        )

    def _event_from_completed(
        self,
        *,
        user_key: str,
        started: float,
        first_chunk_at: float | None,
        completed: DecodeCompleted,
    ) -> UsageEvent:
        meta = completed.result.meta
        return self._base_event(
            _UsageDraft(
                user_key=user_key,
                started=started,
                first_chunk_at=first_chunk_at,
                outcome=UsageOutcome.OK,
                model=meta.model,
                prompt_version=meta.prompt_version,
                latency_ms=meta.latency_ms,
                attempts=meta.attempts,
                usage=meta.usage,
                unavailable_kind=None,
                safety=completed.result.safety.value,
            )
        )

    def _event_from_failure(
        self,
        *,
        user_key: str,
        started: float,
        first_chunk_at: float | None,
        error: GenerationUnavailable | GenerationRefusedByProvider | InvalidGenerationOutput,
    ) -> UsageEvent:
        if isinstance(error, GenerationUnavailable):
            return self._event_from_unavailable(
                user_key=user_key,
                started=started,
                first_chunk_at=first_chunk_at,
                error=error,
            )
        if isinstance(error, GenerationRefusedByProvider):
            return self._event_from_refused(
                user_key=user_key,
                started=started,
                first_chunk_at=first_chunk_at,
                error=error,
            )
        return self._event_from_invalid(
            user_key=user_key,
            started=started,
            first_chunk_at=first_chunk_at,
            error=error,
        )

    def _event_from_unavailable(
        self,
        *,
        user_key: str,
        started: float,
        first_chunk_at: float | None,
        error: GenerationUnavailable,
    ) -> UsageEvent:
        return self._base_event(
            _UsageDraft(
                user_key=user_key,
                started=started,
                first_chunk_at=first_chunk_at,
                outcome=UsageOutcome.UNAVAILABLE,
                model=error.model,
                prompt_version=error.prompt_version,
                latency_ms=0,
                attempts=error.attempts,
                usage=error.usage,
                unavailable_kind=error.kind.value,
                safety=None,
            )
        )

    def _event_from_refused(
        self,
        *,
        user_key: str,
        started: float,
        first_chunk_at: float | None,
        error: GenerationRefusedByProvider,
    ) -> UsageEvent:
        return self._base_event(
            _UsageDraft(
                user_key=user_key,
                started=started,
                first_chunk_at=first_chunk_at,
                outcome=UsageOutcome.REFUSED,
                model=error.model,
                prompt_version=error.prompt_version,
                latency_ms=0,
                attempts=error.attempts,
                usage=error.usage,
                unavailable_kind=None,
                safety=None,
            )
        )

    def _event_from_invalid(
        self,
        *,
        user_key: str,
        started: float,
        first_chunk_at: float | None,
        error: InvalidGenerationOutput,
    ) -> UsageEvent:
        return self._base_event(
            _UsageDraft(
                user_key=user_key,
                started=started,
                first_chunk_at=first_chunk_at,
                outcome=UsageOutcome.INVALID_OUTPUT,
                model=error.model,
                prompt_version=error.prompt_version,
                latency_ms=0,
                attempts=error.attempts,
                usage=error.usage,
                unavailable_kind=None,
                safety=None,
            )
        )

    async def _persist(self, event: UsageEvent) -> None:
        """Write a usage event. Analytics failure never affects the user result."""
        try:
            await self._ports.sink.record(event)
        except UsageEventWriteFailed:
            return


def _screened_decode_completed() -> DecodeCompleted:
    """Empty decode payload for a pre-LLM crisis hit."""
    return DecodeCompleted(
        analysis="",
        result=DecodeResult(
            hypotheses=(),
            underlying_request="",
            variants=(),
            applied_rule_indexes=(),
            safety=SafetyVerdict.CRISIS,
            meta=GenerationMeta(
                model="",
                prompt_version="",
                latency_ms=0,
                attempts=0,
                usage=TokenUsage(),
            ),
        ),
    )
