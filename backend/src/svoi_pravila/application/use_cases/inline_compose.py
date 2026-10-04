"""Compose soften / help-say variants for an inline query (non-streaming)."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    InlineQueryTooShort,
    InvalidGenerationOutput,
    NotFound,
    ScenarioQuotaExceeded,
    UsageEventWriteFailed,
)
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.generation import (
    BOUNDED_TEXT_MAX,
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
from svoi_pravila.application.ports.monotonic import MonotonicClock
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.ports.usage_event_sink import UsageEventSink
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._generation_context import load_active_contact_rule_context
from svoi_pravila.domain.enums import (
    RelationshipKind,
    UsageEventKind,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import TelegramUserId, UsageEventId
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


@dataclass(frozen=True, slots=True)
class InlineComposePorts:
    """Injected collaborators for InlineCompose."""

    uow_factory: UnitOfWorkFactory
    catalog: ConsentCatalog
    generator: TextGenerator
    quota: RateLimiter
    sink: UsageEventSink
    clock: Clock
    monotonic: MonotonicClock
    ids: IdGenerator
    pseudonymizer: Pseudonymizer
    min_chars: int
    deadline_seconds: float
    intent_prefixes: tuple[tuple[str, HelpSayIntent], ...]


@dataclass(frozen=True, slots=True)
class _UsageDraft:
    """C0 fields for a usage event before timestamps are filled in."""

    user_key: str
    started: float
    outcome: UsageOutcome
    model: str
    prompt_version: str
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
        stripped = command.query.strip()
        if len(stripped) > BOUNDED_TEXT_MAX:
            raise IncomingTextTooLong()
        if len(stripped) < self._ports.min_chars:
            raise InlineQueryTooShort()
        matched = _match_prefix(stripped, self._ports.intent_prefixes)
        scenario = UsageScenario.SOFTEN
        draft = stripped
        intent: HelpSayIntent | None = None
        if matched is not None:
            intent, remainder = matched
            if len(remainder) < self._ports.min_chars:
                raise InlineQueryTooShort()
            scenario = UsageScenario.HELP_SAY
            draft = remainder
        quota_pseudonym = self._ports.pseudonymizer.pseudonymize(_QUOTA_PURPOSE, user_key)
        decision = await self._ports.quota.check(quota_pseudonym)
        if not decision.allowed:
            raise ScenarioQuotaExceeded()
        started = self._ports.monotonic.monotonic()
        try:
            if intent is not None:
                generated: SoftenResult | HelpSayResult = await self._ports.generator.help_say(
                    HelpSayRequest(
                        intent=intent,
                        details=draft,
                        rules=rules,
                        relationship=relationship,
                        deadline_seconds=self._ports.deadline_seconds,
                    )
                )
            else:
                generated = await self._ports.generator.soften(
                    SoftenRequest(
                        draft=draft,
                        rules=rules,
                        relationship=relationship,
                        deadline_seconds=self._ports.deadline_seconds,
                    )
                )
        except GenerationUnavailable as exc:
            await self._persist(self._event_from_error(user_key, started, scenario, exc))
            raise
        except GenerationRefusedByProvider as exc:
            await self._persist(self._event_from_error(user_key, started, scenario, exc))
            raise
        except InvalidGenerationOutput as exc:
            await self._persist(self._event_from_error(user_key, started, scenario, exc))
            raise
        await self._persist(self._event_from_ok(user_key, started, scenario, generated))
        return InlineComposeResult(
            scenario=scenario,
            variants=generated.variants,
            safety=generated.safety,
        )

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
            latency_ms=draft.latency_ms if draft.outcome is UsageOutcome.OK else measured,
            ttfc_ms=None,
            attempts=draft.attempts,
            input_tokens=draft.usage.input,
            output_tokens=draft.usage.output,
            billable_tokens=draft.usage.billable,
            event_kind=UsageEventKind.GENERATION,
            variant_firmness=None,
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
