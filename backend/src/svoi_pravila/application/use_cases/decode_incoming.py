"""Decode an incoming private-chat message with streaming generation."""

from __future__ import annotations

from asyncio import CancelledError
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, replace
from datetime import date
from typing import Protocol

from svoi_pravila.application.applied_rules import applied_rule_views
from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.errors import (
    CacheUnavailable,
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    IncomingTextTooShort,
    InvalidGenerationOutput,
    NotFound,
    ScenarioBusy,
    ServiceBudgetExhausted,
    UsageEventWriteFailed,
    UserQuotaExhausted,
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
from svoi_pravila.application.ports.llm_budget import BudgetExhausted, LlmBudget
from svoi_pravila.application.ports.monotonic import MonotonicClock
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.quota_gate import (
    QuotaExhausted,
    QuotaGate,
    QuotaReservation,
)
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.ports.usage_event_sink import UsageEventSink
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._cancelled_budget import (
    CancelledGeneration,
    handle_generation_cancelled,
)
from svoi_pravila.application.use_cases._generation_context import load_active_contact_rule_context
from svoi_pravila.application.use_cases._limits import generation_unavailable_from_cache
from svoi_pravila.domain.enums import (
    LimitKind,
    QuotaClass,
    RelationshipKind,
    UsageEventKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import TelegramUserId, UsageEventId
from svoi_pravila.domain.product_day import product_day
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
    surface: UsageSurface


@dataclass(frozen=True, slots=True)
class DecodeIncomingPorts:
    """Injected collaborators for DecodeIncoming."""

    uow_factory: UnitOfWorkFactory
    catalog: ConsentCatalog
    generator: TextGenerator
    guard: ConcurrencyGuard
    quota_gate: QuotaGate
    llm_budget: LlmBudget
    sink: UsageEventSink
    clock: Clock
    monotonic: MonotonicClock
    ids: IdGenerator
    pseudonymizer: Pseudonymizer
    crisis_screen: CrisisScreen
    deadline_seconds: float
    analytics_timezone: str


class IncomingDecoder(Protocol):
    """Streaming decode use case as seen by the channel adapter."""

    def execute(self, command: DecodeIncomingCommand) -> AsyncIterator[DecodeEvent]:
        """Yield analysis chunks then a completed result, or raise a typed error."""
        ...


@dataclass(frozen=True, slots=True)
class _UsageDraft:
    """C0 fields for a usage event before timestamps are filled in."""

    user_key: str
    surface: UsageSurface
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


@dataclass(frozen=True, slots=True)
class _StreamArgs:
    """Provider-stream inputs after quota reservation."""

    text: str
    user_key: str
    surface: UsageSurface
    relationship: RelationshipKind
    rules: tuple[RuleContext, ...]
    day: date
    started: float


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
        surface = command.surface
        if self._ports.crisis_screen.hit(text):
            completed = _screened_decode_completed()
            await self._persist(self._event_from_screened(user_key, surface))
            yield completed
            return
        lock_pseudonym = self._ports.pseudonymizer.pseudonymize(_RATE_LIMIT_PURPOSE, user_key)
        lock_key = f"tg:decode:lock:{lock_pseudonym}"
        ttl = int(self._ports.deadline_seconds) + _LOCK_MARGIN_SECONDS
        token = await self._ports.guard.acquire(lock_key, ttl_seconds=ttl)
        if token is None:
            raise ScenarioBusy()
        try:
            async for event in self._produce(
                text=text,
                user_key=user_key,
                surface=surface,
                relationship=relationship,
                rules=rules,
            ):
                yield event
        finally:
            await self._ports.guard.release(lock_key, token)

    async def _produce(
        self,
        *,
        text: str,
        user_key: str,
        surface: UsageSurface,
        relationship: RelationshipKind,
        rules: tuple[RuleContext, ...],
    ) -> AsyncGenerator[DecodeEvent]:
        # Normative order after lock: budget check → quota reserve → provider → persist → budget.add
        day = product_day(self._ports.clock.now(), self._ports.analytics_timezone)
        reservation: QuotaReservation | None = None
        started = self._ports.monotonic.monotonic()
        first_chunk_at: list[float] = []
        provider_started = [False]
        try:
            reservation = await self._reserve(user_key=user_key, surface=surface, day=day)
            started = self._ports.monotonic.monotonic()
            stream = _StreamArgs(
                text=text,
                user_key=user_key,
                surface=surface,
                relationship=relationship,
                rules=rules,
                day=day,
                started=started,
            )
            async for event in self._stream_provider(stream, first_chunk_at, provider_started):
                yield event
        except GenerationUnavailable as exc:
            if reservation is not None:
                await self._ports.quota_gate.refund(reservation)
            await self._persist(
                self._event_from_failure(
                    user_key=user_key,
                    surface=surface,
                    started=started,
                    first_chunk_at=first_chunk_at[0] if first_chunk_at else None,
                    error=exc,
                )
            )
            raise
        except (GenerationRefusedByProvider, InvalidGenerationOutput) as exc:
            await self._persist(
                self._event_from_failure(
                    user_key=user_key,
                    surface=surface,
                    started=started,
                    first_chunk_at=first_chunk_at[0] if first_chunk_at else None,
                    error=exc,
                )
            )
            await self._budget_add(day, exc.usage.billable)
            raise
        except CancelledError:
            await handle_generation_cancelled(
                CancelledGeneration(
                    provider_started=provider_started[0],
                    reservation=reservation,
                    quota_gate=self._ports.quota_gate,
                    llm_budget=self._ports.llm_budget,
                    day=day,
                    billable_tokens=self._ports.generator.max_billable(
                        DecodeRequest(
                            incoming=text,
                            rules=rules,
                            relationship=relationship,
                            deadline_seconds=self._ports.deadline_seconds,
                        )
                    ),
                )
            )
            raise

    async def _reserve(
        self, *, user_key: str, surface: UsageSurface, day: date
    ) -> QuotaReservation:
        """Budget check then DECODE quota reserve; raises typed limit / cache errors."""
        try:
            budget = await self._ports.llm_budget.check(day)
        except CacheUnavailable as exc:
            raise generation_unavailable_from_cache(exc) from exc
        if isinstance(budget, BudgetExhausted):
            await self._persist(
                self._event_from_limited(user_key, surface, LimitKind.GLOBAL_BUDGET)
            )
            raise ServiceBudgetExhausted(resets_at=budget.resets_at)
        quota_pseudonym = self._ports.pseudonymizer.pseudonymize(_QUOTA_PURPOSE, user_key)
        try:
            decision = await self._ports.quota_gate.reserve(quota_pseudonym, QuotaClass.DECODE, day)
        except CacheUnavailable as exc:
            raise generation_unavailable_from_cache(exc) from exc
        if isinstance(decision, QuotaExhausted):
            await self._persist(self._event_from_limited(user_key, surface, LimitKind.USER_QUOTA))
            raise UserQuotaExhausted(resets_at=decision.resets_at)
        return decision.reservation

    async def _stream_provider(
        self,
        args: _StreamArgs,
        first_chunk_at: list[float],
        provider_started: list[bool],
    ) -> AsyncGenerator[DecodeEvent]:
        request = DecodeRequest(
            incoming=args.text,
            rules=args.rules,
            relationship=args.relationship,
            deadline_seconds=self._ports.deadline_seconds,
        )
        provider_started[0] = True
        async for event in self._ports.generator.decode_stream(request):
            if isinstance(event, AnalysisChunk):
                if not first_chunk_at:
                    first_chunk_at.append(self._ports.monotonic.monotonic())
                yield event
                continue
            completed = replace(
                event,
                applied_rules=applied_rule_views(args.rules, event.result.applied_rule_indexes),
            )
            yield completed
            await self._persist(
                self._event_from_completed(
                    user_key=args.user_key,
                    surface=args.surface,
                    started=args.started,
                    first_chunk_at=first_chunk_at[0] if first_chunk_at else None,
                    completed=completed,
                )
            )
            await self._budget_add(args.day, completed.result.meta.usage.billable)

    async def _budget_add(self, day: date, billable_tokens: int) -> None:
        try:
            await self._ports.llm_budget.add(day, billable_tokens)
        except CacheUnavailable as exc:
            raise generation_unavailable_from_cache(exc) from exc

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
            surface=draft.surface,
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

    def _event_from_screened(self, user_key: str, surface: UsageSurface) -> UsageEvent:
        return self._base_event(
            _UsageDraft(
                user_key=user_key,
                surface=surface,
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

    def _event_from_limited(
        self, user_key: str, surface: UsageSurface, limit_kind: LimitKind
    ) -> UsageEvent:
        occurred = self._ports.clock.now().replace(microsecond=0)
        analytics = self._ports.pseudonymizer.pseudonymize(_ANALYTICS_PURPOSE, user_key)
        return UsageEvent(
            id=UsageEventId(self._ports.ids.new_id()),
            occurred_at=occurred,
            user_pseudonym=analytics,
            scenario=UsageScenario.DECODE,
            surface=surface,
            outcome=UsageOutcome.LIMITED,
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
            event_kind=UsageEventKind.GENERATION,
            variant_firmness=None,
            limit_kind=limit_kind,
        )

    def _event_from_completed(
        self,
        *,
        user_key: str,
        surface: UsageSurface,
        started: float,
        first_chunk_at: float | None,
        completed: DecodeCompleted,
    ) -> UsageEvent:
        meta = completed.result.meta
        return self._base_event(
            _UsageDraft(
                user_key=user_key,
                surface=surface,
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
        surface: UsageSurface,
        started: float,
        first_chunk_at: float | None,
        error: GenerationUnavailable | GenerationRefusedByProvider | InvalidGenerationOutput,
    ) -> UsageEvent:
        if isinstance(error, GenerationUnavailable):
            return self._event_from_unavailable(
                user_key=user_key,
                surface=surface,
                started=started,
                first_chunk_at=first_chunk_at,
                error=error,
            )
        if isinstance(error, GenerationRefusedByProvider):
            return self._event_from_refused(
                user_key=user_key,
                surface=surface,
                started=started,
                first_chunk_at=first_chunk_at,
                error=error,
            )
        return self._event_from_invalid(
            user_key=user_key,
            surface=surface,
            started=started,
            first_chunk_at=first_chunk_at,
            error=error,
        )

    def _event_from_unavailable(
        self,
        *,
        user_key: str,
        surface: UsageSurface,
        started: float,
        first_chunk_at: float | None,
        error: GenerationUnavailable,
    ) -> UsageEvent:
        return self._base_event(
            _UsageDraft(
                user_key=user_key,
                surface=surface,
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
        surface: UsageSurface,
        started: float,
        first_chunk_at: float | None,
        error: GenerationRefusedByProvider,
    ) -> UsageEvent:
        return self._base_event(
            _UsageDraft(
                user_key=user_key,
                surface=surface,
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
        surface: UsageSurface,
        started: float,
        first_chunk_at: float | None,
        error: InvalidGenerationOutput,
    ) -> UsageEvent:
        return self._base_event(
            _UsageDraft(
                user_key=user_key,
                surface=surface,
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
