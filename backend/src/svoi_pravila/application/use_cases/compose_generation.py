"""Shared soften / help-say generation for inline and mini-app surfaces."""

from __future__ import annotations

from asyncio import CancelledError
from dataclasses import dataclass
from datetime import date

from svoi_pravila.application.applied_rules import applied_rule_views
from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.errors import (
    CacheUnavailable,
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    IncomingTextTooShort,
    InlineProduceError,
    InvalidGenerationOutput,
    NotFound,
    ServiceBudgetExhausted,
    UsageEventWriteFailed,
    UserQuotaExhausted,
)
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.generation import (
    BOUNDED_TEXT_MAX,
    BOUNDED_TEXT_MIN,
    AppliedRuleView,
    HelpSayIntent,
    HelpSayRequest,
    HelpSayResult,
    RuleContext,
    SafetyVerdict,
    SoftenRequest,
    SoftenResult,
    TextGenerator,
    TokenUsage,
    Variant,
)
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.inline_result_reuse import InlineReuseValue
from svoi_pravila.application.ports.llm_budget import BudgetExhausted, LlmBudget
from svoi_pravila.application.ports.monotonic import MonotonicClock
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.quota_gate import QuotaExhausted, QuotaGate, Reserved
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.ports.usage_event_sink import UsageEventSink
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._cancelled_budget import (
    CancelledGeneration,
    handle_generation_cancelled,
)
from svoi_pravila.application.use_cases._generation_context import load_contact_rule_context
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
from svoi_pravila.domain.ids import ContactId, TelegramUserId, UsageEventId
from svoi_pravila.domain.product_day import product_day
from svoi_pravila.domain.usage import UsageEvent

_QUOTA_PURPOSE = "inline_quota"
_ANALYTICS_PURPOSE = "analytics"


@dataclass(frozen=True, slots=True)
class ComposeGenerationCommand:
    """Input for ComposeGeneration (mini-app and direct callers)."""

    telegram_user_id: TelegramUserId
    draft: str
    intent: HelpSayIntent | None
    contact_id: ContactId | None
    surface: UsageSurface


@dataclass(frozen=True, slots=True)
class ComposeGenerationResult:
    """Variants or a safety screen; never streams."""

    scenario: UsageScenario
    variants: tuple[Variant, ...]
    safety: SafetyVerdict
    applied_rules: tuple[AppliedRuleView, ...] = ()


@dataclass(frozen=True, slots=True)
class ComposeProduceMaterial:
    """Pre-resolved context for the produce path (inline reuse and mini-app)."""

    user_key: str
    scenario: UsageScenario
    intent: HelpSayIntent | None
    draft: str
    relationship: RelationshipKind
    rules: tuple[RuleContext, ...]
    surface: UsageSurface


@dataclass(frozen=True, slots=True)
class ComposeGenerationPorts:
    """Injected collaborators for ComposeGeneration."""

    uow_factory: UnitOfWorkFactory
    catalog: ConsentCatalog
    generator: TextGenerator
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


@dataclass(frozen=True, slots=True)
class _UsageDraft:
    """C0 fields for a usage event before timestamps are filled in."""

    user_key: str
    surface: UsageSurface
    started: float
    outcome: UsageOutcome
    model: str | None
    prompt_version: str | None
    latency_ms: int
    attempts: int
    usage: TokenUsage
    unavailable_kind: str | None
    safety: str | None
    scenario: UsageScenario


class ComposeGeneration:
    """Produce soften or help-say variants for inline or mini-app surfaces."""

    def __init__(self, ports: ComposeGenerationPorts) -> None:
        self._ports = ports

    async def execute(self, command: ComposeGenerationCommand) -> ComposeGenerationResult:
        """Load context, screen crisis, generate; raise typed limit/provider errors."""
        draft = command.draft
        if len(draft) < BOUNDED_TEXT_MIN:
            raise IncomingTextTooShort()
        if len(draft) > BOUNDED_TEXT_MAX:
            raise IncomingTextTooLong()
        scenario = UsageScenario.HELP_SAY if command.intent is not None else UsageScenario.SOFTEN
        user_key = str(command.telegram_user_id.value)
        relationship, rules = await self._load_context(command.telegram_user_id, command.contact_id)
        material = ComposeProduceMaterial(
            user_key=user_key,
            scenario=scenario,
            intent=command.intent,
            draft=draft,
            relationship=relationship,
            rules=rules,
            surface=command.surface,
        )
        if self._ports.crisis_screen.hit(draft):
            await self._persist(self._event_from_screened(material))
            return ComposeGenerationResult(
                scenario=scenario,
                variants=(),
                safety=SafetyVerdict.CRISIS,
                applied_rules=(),
            )
        produced = await self.produce(material)
        if isinstance(
            produced,
            (
                UserQuotaExhausted,
                ServiceBudgetExhausted,
                GenerationUnavailable,
                GenerationRefusedByProvider,
                InvalidGenerationOutput,
            ),
        ):
            raise produced
        return ComposeGenerationResult(
            scenario=produced.scenario,
            variants=produced.variants,
            safety=produced.safety,
            applied_rules=produced.applied_rules,
        )

    async def produce(
        self, material: ComposeProduceMaterial
    ) -> InlineReuseValue | InlineProduceError:
        """Budget → quota → provider → usage → budget.add. Used by inline reuse."""
        day = product_day(self._ports.clock.now(), self._ports.analytics_timezone)
        reserved = await self._budget_and_reserve(material, day)
        if not isinstance(reserved, Reserved):
            return reserved
        return await self._generate_after_reserve(material, day, reserved)

    def is_crisis(self, draft: str) -> bool:
        """True when the deterministic crisis screen hits ``draft``."""
        return self._ports.crisis_screen.hit(draft)

    async def record_screened(self, material: ComposeProduceMaterial) -> None:
        """Persist a crisis-screened generation event (inline path before reuse)."""
        await self._persist(self._event_from_screened(material))

    async def _budget_and_reserve(
        self, material: ComposeProduceMaterial, day: date
    ) -> Reserved | InlineProduceError:
        try:
            budget = await self._ports.llm_budget.check(day)
        except CacheUnavailable as exc:
            return generation_unavailable_from_cache(exc)
        if isinstance(budget, BudgetExhausted):
            await self._persist(self._event_from_limited(material, LimitKind.GLOBAL_BUDGET))
            return ServiceBudgetExhausted(resets_at=budget.resets_at)
        quota_pseudonym = self._ports.pseudonymizer.pseudonymize(_QUOTA_PURPOSE, material.user_key)
        try:
            decision = await self._ports.quota_gate.reserve(quota_pseudonym, QuotaClass.INLINE, day)
        except CacheUnavailable as exc:
            return generation_unavailable_from_cache(exc)
        if isinstance(decision, QuotaExhausted):
            await self._persist(self._event_from_limited(material, LimitKind.USER_QUOTA))
            return UserQuotaExhausted(resets_at=decision.resets_at)
        return decision

    async def _generate_after_reserve(
        self, material: ComposeProduceMaterial, day: date, reserved: Reserved
    ) -> InlineReuseValue | InlineProduceError:
        reservation = reserved.reservation
        started = self._ports.monotonic.monotonic()
        provider_started = False
        try:
            provider_started = True
            generated = await self._call_generator(material)
        except GenerationUnavailable as exc:
            await self._ports.quota_gate.refund(reservation)
            await self._persist(self._event_from_error(material, started, exc))
            return exc
        except GenerationRefusedByProvider as exc:
            await self._persist(self._event_from_error(material, started, exc))
            await self._budget_add(day, exc.usage.billable)
            return exc
        except InvalidGenerationOutput as exc:
            await self._persist(self._event_from_error(material, started, exc))
            await self._budget_add(day, exc.usage.billable)
            return exc
        except CancelledError:
            await handle_generation_cancelled(
                CancelledGeneration(
                    provider_started=provider_started,
                    reservation=reservation,
                    quota_gate=self._ports.quota_gate,
                    llm_budget=self._ports.llm_budget,
                    day=day,
                    billable_tokens=self._ports.generator.max_billable(
                        self._generation_request(material)
                    ),
                )
            )
            raise
        event = self._event_from_ok(material, started, generated)
        await self._persist(event)
        await self._budget_add(day, generated.meta.usage.billable)
        return InlineReuseValue(
            scenario=material.scenario,
            variants=generated.variants,
            safety=generated.safety,
            applied_rules=applied_rule_views(material.rules, generated.applied_rule_indexes),
        )

    def _generation_request(
        self, material: ComposeProduceMaterial
    ) -> SoftenRequest | HelpSayRequest:
        if material.intent is not None:
            return HelpSayRequest(
                intent=material.intent,
                details=material.draft,
                rules=material.rules,
                relationship=material.relationship,
                deadline_seconds=self._ports.deadline_seconds,
            )
        return SoftenRequest(
            draft=material.draft,
            rules=material.rules,
            relationship=material.relationship,
            deadline_seconds=self._ports.deadline_seconds,
        )

    async def _call_generator(
        self, material: ComposeProduceMaterial
    ) -> SoftenResult | HelpSayResult:
        request = self._generation_request(material)
        if isinstance(request, HelpSayRequest):
            return await self._ports.generator.help_say(request)
        return await self._ports.generator.soften(request)

    async def _budget_add(self, day: date, billable_tokens: int) -> None:
        try:
            await self._ports.llm_budget.add(day, billable_tokens)
        except CacheUnavailable as exc:
            raise generation_unavailable_from_cache(exc) from exc

    async def _load_context(
        self,
        telegram_user_id: TelegramUserId,
        contact_id: ContactId | None,
    ) -> tuple[RelationshipKind, tuple[RuleContext, ...]]:
        async with self._ports.uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                raise NotFound()
            await require_access(uow, self._ports.catalog, user.id)
            return await load_contact_rule_context(uow, user, contact_id)

    def _base_event(self, draft: _UsageDraft) -> UsageEvent:
        ended = self._ports.monotonic.monotonic()
        measured = max(0, int((ended - draft.started) * 1000))
        occurred = self._ports.clock.now().replace(microsecond=0)
        analytics = self._ports.pseudonymizer.pseudonymize(_ANALYTICS_PURPOSE, draft.user_key)
        return UsageEvent(
            id=UsageEventId(self._ports.ids.new_id()),
            occurred_at=occurred,
            user_pseudonym=analytics,
            scenario=draft.scenario,
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
            ttfc_ms=None,
            attempts=draft.attempts,
            input_tokens=draft.usage.input,
            output_tokens=draft.usage.output,
            billable_tokens=draft.usage.billable,
            event_kind=UsageEventKind.GENERATION,
            variant_firmness=None,
        )

    def _event_from_screened(self, material: ComposeProduceMaterial) -> UsageEvent:
        return self._base_event(
            _UsageDraft(
                user_key=material.user_key,
                surface=material.surface,
                started=self._ports.monotonic.monotonic(),
                outcome=UsageOutcome.SCREENED,
                model=None,
                prompt_version=None,
                latency_ms=0,
                attempts=0,
                usage=TokenUsage(),
                unavailable_kind=None,
                safety=SafetyVerdict.CRISIS.value,
                scenario=material.scenario,
            )
        )

    def _event_from_limited(
        self, material: ComposeProduceMaterial, limit_kind: LimitKind
    ) -> UsageEvent:
        occurred = self._ports.clock.now().replace(microsecond=0)
        analytics = self._ports.pseudonymizer.pseudonymize(_ANALYTICS_PURPOSE, material.user_key)
        return UsageEvent(
            id=UsageEventId(self._ports.ids.new_id()),
            occurred_at=occurred,
            user_pseudonym=analytics,
            scenario=material.scenario,
            surface=material.surface,
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

    def _event_from_ok(
        self,
        material: ComposeProduceMaterial,
        started: float,
        generated: SoftenResult | HelpSayResult,
    ) -> UsageEvent:
        meta = generated.meta
        return self._base_event(
            _UsageDraft(
                user_key=material.user_key,
                surface=material.surface,
                started=started,
                outcome=UsageOutcome.OK,
                model=meta.model,
                prompt_version=meta.prompt_version,
                latency_ms=meta.latency_ms,
                attempts=meta.attempts,
                usage=meta.usage,
                unavailable_kind=None,
                safety=generated.safety.value,
                scenario=material.scenario,
            )
        )

    def _event_from_error(
        self,
        material: ComposeProduceMaterial,
        started: float,
        error: GenerationUnavailable | GenerationRefusedByProvider | InvalidGenerationOutput,
    ) -> UsageEvent:
        if isinstance(error, GenerationUnavailable):
            outcome = UsageOutcome.UNAVAILABLE
            unavailable_kind = error.kind.value
        elif isinstance(error, GenerationRefusedByProvider):
            outcome = UsageOutcome.REFUSED
            unavailable_kind = None
        else:
            outcome = UsageOutcome.INVALID_OUTPUT
            unavailable_kind = None
        return self._base_event(
            _UsageDraft(
                user_key=material.user_key,
                surface=material.surface,
                started=started,
                outcome=outcome,
                model=error.model,
                prompt_version=error.prompt_version,
                latency_ms=0,
                attempts=error.attempts,
                usage=error.usage,
                unavailable_kind=unavailable_kind,
                safety=None,
                scenario=material.scenario,
            )
        )

    async def _persist(self, event: UsageEvent) -> None:
        """Write a usage event. Analytics failure never affects the user result."""
        try:
            await self._ports.sink.record(event)
        except UsageEventWriteFailed:
            return
