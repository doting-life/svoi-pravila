"""Compose soften / help-say variants for an inline query (non-streaming)."""

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
    InlineComposeFailed,
    InlineProduceError,
    InlineQueryTooShort,
    InvalidGenerationOutput,
    NotFound,
    ServiceBudgetExhausted,
    UsageEventWriteFailed,
    UserQuotaExhausted,
)
from svoi_pravila.application.inline_reuse_key import InlineReuseKeyMaterial, inline_reuse_key
from svoi_pravila.application.inline_reuse_status import InlineReuseStatus
from svoi_pravila.application.inline_text import normalize_inline_text
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.generation import (
    BOUNDED_TEXT_MAX,
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
from svoi_pravila.application.ports.inline_result_reuse import (
    InlineResultReuse,
    InlineReuseValue,
    ReuseFailed,
)
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

_QUOTA_PURPOSE = "inline_quota"
_ANALYTICS_PURPOSE = "analytics"


@dataclass(frozen=True, slots=True)
class InlineComposeCommand:
    """Input for InlineCompose."""

    telegram_user_id: TelegramUserId
    query: str


@dataclass(frozen=True, slots=True)
class InlineComposeResult:
    """Typed variants for ``answerInlineQuery`` (never streamed)."""

    scenario: UsageScenario
    variants: tuple[Variant, ...]
    safety: SafetyVerdict
    applied_rules: tuple[AppliedRuleView, ...] = ()
    reuse: InlineReuseStatus | None = None


@dataclass(frozen=True, slots=True)
class InlineComposePorts:
    """Injected collaborators for InlineCompose."""

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
    reuse: InlineResultReuse
    min_chars: int
    deadline_seconds: float
    intent_prefixes: tuple[tuple[str, HelpSayIntent], ...]
    analytics_timezone: str


@dataclass(frozen=True, slots=True)
class _UsageDraft:
    """C0 fields for a usage event before timestamps are filled in."""

    user_key: str
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


class InlineCompose:
    """Produce inline soften or help-say variants for a fully onboarded user."""

    def __init__(self, ports: InlineComposePorts) -> None:
        self._ports = ports

    async def execute(self, command: InlineComposeCommand) -> InlineComposeResult:
        """Return variants or raise a typed error. Never streams."""
        user_key = str(command.telegram_user_id.value)
        relationship, rules = await self._load_context(command.telegram_user_id)
        normalized = normalize_inline_text(command.query)
        if len(normalized) > BOUNDED_TEXT_MAX:
            raise IncomingTextTooLong()
        if len(normalized) < self._ports.min_chars:
            raise InlineQueryTooShort()
        matched = _match_prefix(normalized, self._ports.intent_prefixes)
        scenario = UsageScenario.SOFTEN
        draft = normalized
        intent: HelpSayIntent | None = None
        if matched is not None:
            intent, remainder = matched
            draft = normalize_inline_text(remainder)
            if len(draft) < self._ports.min_chars:
                raise InlineQueryTooShort()
            scenario = UsageScenario.HELP_SAY
        if self._ports.crisis_screen.hit(draft):
            await self._persist(self._event_from_screened(user_key, scenario))
            return InlineComposeResult(
                scenario=scenario,
                variants=(),
                safety=SafetyVerdict.CRISIS,
                applied_rules=(),
                reuse=None,
            )
        material = InlineReuseKeyMaterial(
            user_key=user_key,
            scenario=scenario,
            intent=intent,
            draft=draft,
            relationship=relationship,
            rules=rules,
        )
        key = inline_reuse_key(material)

        async def produce() -> InlineReuseValue | InlineProduceError:
            return await self._produce(material)

        resolution = await self._ports.reuse.resolve(key, user_key, produce)
        if isinstance(resolution, ReuseFailed):
            raise InlineComposeFailed(
                resolution.error,
                reuse=resolution.status,
            ) from resolution.error
        return InlineComposeResult(
            scenario=resolution.value.scenario,
            variants=resolution.value.variants,
            safety=resolution.value.safety,
            applied_rules=resolution.value.applied_rules,
            reuse=resolution.status,
        )

    async def _produce(
        self, material: InlineReuseKeyMaterial
    ) -> InlineReuseValue | InlineProduceError:
        # Normative order: budget check → quota reserve → provider → persist → budget.add
        day = product_day(self._ports.clock.now(), self._ports.analytics_timezone)
        reserved = await self._budget_and_reserve(material, day)
        if not isinstance(reserved, Reserved):
            return reserved
        return await self._generate_after_reserve(material, day, reserved)

    async def _budget_and_reserve(
        self, material: InlineReuseKeyMaterial, day: date
    ) -> Reserved | InlineProduceError:
        try:
            budget = await self._ports.llm_budget.check(day)
        except CacheUnavailable as exc:
            return generation_unavailable_from_cache(exc)
        if isinstance(budget, BudgetExhausted):
            await self._persist(
                self._event_from_limited(
                    material.user_key, material.scenario, LimitKind.GLOBAL_BUDGET
                )
            )
            return ServiceBudgetExhausted(resets_at=budget.resets_at)
        quota_pseudonym = self._ports.pseudonymizer.pseudonymize(_QUOTA_PURPOSE, material.user_key)
        try:
            decision = await self._ports.quota_gate.reserve(quota_pseudonym, QuotaClass.INLINE, day)
        except CacheUnavailable as exc:
            return generation_unavailable_from_cache(exc)
        if isinstance(decision, QuotaExhausted):
            await self._persist(
                self._event_from_limited(material.user_key, material.scenario, LimitKind.USER_QUOTA)
            )
            return UserQuotaExhausted(resets_at=decision.resets_at)
        return decision

    async def _generate_after_reserve(
        self, material: InlineReuseKeyMaterial, day: date, reserved: Reserved
    ) -> InlineReuseValue | InlineProduceError:
        reservation = reserved.reservation
        started = self._ports.monotonic.monotonic()
        provider_started = False
        try:
            provider_started = True
            generated = await self._call_generator(material)
        except GenerationUnavailable as exc:
            await self._ports.quota_gate.refund(reservation)
            await self._persist(
                self._event_from_error(material.user_key, started, material.scenario, exc)
            )
            return exc
        except GenerationRefusedByProvider as exc:
            await self._persist(
                self._event_from_error(material.user_key, started, material.scenario, exc)
            )
            await self._budget_add(day, exc.usage.billable)
            return exc
        except InvalidGenerationOutput as exc:
            await self._persist(
                self._event_from_error(material.user_key, started, material.scenario, exc)
            )
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
        event = self._event_from_ok(material.user_key, started, material.scenario, generated)
        await self._persist(event)
        await self._budget_add(day, generated.meta.usage.billable)
        return InlineReuseValue(
            scenario=material.scenario,
            variants=generated.variants,
            safety=generated.safety,
            applied_rules=applied_rule_views(material.rules, generated.applied_rule_indexes),
        )

    def _generation_request(
        self, material: InlineReuseKeyMaterial
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
        self, material: InlineReuseKeyMaterial
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
        occurred = self._ports.clock.now().replace(microsecond=0)
        analytics = self._ports.pseudonymizer.pseudonymize(_ANALYTICS_PURPOSE, draft.user_key)
        return UsageEvent(
            id=UsageEventId(self._ports.ids.new_id()),
            occurred_at=occurred,
            user_pseudonym=analytics,
            scenario=draft.scenario,
            surface=UsageSurface.INLINE,
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

    def _event_from_screened(self, user_key: str, scenario: UsageScenario) -> UsageEvent:
        return self._base_event(
            _UsageDraft(
                user_key=user_key,
                started=self._ports.monotonic.monotonic(),
                outcome=UsageOutcome.SCREENED,
                model=None,
                prompt_version=None,
                latency_ms=0,
                attempts=0,
                usage=TokenUsage(),
                unavailable_kind=None,
                safety=SafetyVerdict.CRISIS.value,
                scenario=scenario,
            )
        )

    def _event_from_limited(
        self, user_key: str, scenario: UsageScenario, limit_kind: LimitKind
    ) -> UsageEvent:
        occurred = self._ports.clock.now().replace(microsecond=0)
        analytics = self._ports.pseudonymizer.pseudonymize(_ANALYTICS_PURPOSE, user_key)
        return UsageEvent(
            id=UsageEventId(self._ports.ids.new_id()),
            occurred_at=occurred,
            user_pseudonym=analytics,
            scenario=scenario,
            surface=UsageSurface.INLINE,
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
        user_key: str,
        started: float,
        scenario: UsageScenario,
        generated: SoftenResult | HelpSayResult,
    ) -> UsageEvent:
        meta = generated.meta
        return self._base_event(
            _UsageDraft(
                user_key=user_key,
                started=started,
                outcome=UsageOutcome.OK,
                model=meta.model,
                prompt_version=meta.prompt_version,
                latency_ms=meta.latency_ms,
                attempts=meta.attempts,
                usage=meta.usage,
                unavailable_kind=None,
                safety=generated.safety.value,
                scenario=scenario,
            )
        )

    def _event_from_error(
        self,
        user_key: str,
        started: float,
        scenario: UsageScenario,
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
                user_key=user_key,
                started=started,
                outcome=outcome,
                model=error.model,
                prompt_version=error.prompt_version,
                latency_ms=0,
                attempts=error.attempts,
                usage=error.usage,
                unavailable_kind=unavailable_kind,
                safety=None,
                scenario=scenario,
            )
        )

    async def _persist(self, event: UsageEvent) -> None:
        """Write a usage event. Analytics failure never affects the user result."""
        try:
            await self._ports.sink.record(event)
        except UsageEventWriteFailed:
            return


def _match_prefix(
    query: str,
    prefixes: tuple[tuple[str, HelpSayIntent], ...],
) -> tuple[HelpSayIntent, str] | None:
    folded = query.casefold()
    ordered = sorted(prefixes, key=lambda item: len(item[0]), reverse=True)
    for prefix, intent in ordered:
        needle = prefix.strip().casefold()
        if not needle:
            continue
        if folded.startswith(needle):
            remainder = query[len(prefix.strip()) :].strip()
            return intent, remainder
    return None
