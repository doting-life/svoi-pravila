"""Suggest a rule candidate from a one-time sealed decode source token."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from enum import StrEnum

from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.errors import (
    ConflictError,
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    NotFound,
    RuleSourceUnavailable,
    UsageEventWriteFailed,
)
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.generation import (
    RuleContext,
    SuggestRuleNothing,
    SuggestRuleProposed,
    SuggestRuleRequest,
    SuggestRuleResult,
    TextGenerator,
)
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.monotonic import MonotonicClock
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.ports.rule_sources import RuleSources
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.ports.usage_event_sink import UsageEventSink
from svoi_pravila.application.rule_source import RuleSourcePayload
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._contact_access import load_owned_contact
from svoi_pravila.application.use_cases._effective_rules import collect_effective_rules
from svoi_pravila.domain.enums import (
    RelationshipKind,
    SuggestionSource,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import (
    ContactId,
    RuleSuggestionId,
    TelegramUserId,
    UsageEventId,
    UserId,
)
from svoi_pravila.domain.rule_suggestion import RuleSuggestion
from svoi_pravila.domain.usage import UsageEvent

_ANALYTICS_PURPOSE = "analytics"
RULE_SOURCE_PURPOSE = "rule_source"
_QUOTA_PURPOSE = "suggest_quota"


class SuggestRuleFromDecodeOutcome(StrEnum):
    """Typed outcomes for SuggestRuleFromDecode (expected cases)."""

    OK = "ok"
    NONE = "none"
    UNAVAILABLE = "unavailable"
    CRISIS = "crisis"
    QUOTA_EXCEEDED = "quota_exceeded"
    PENDING_EXISTS = "pending_exists"


@dataclass(frozen=True, slots=True)
class SuggestRuleFromDecodeCommand:
    """Input for SuggestRuleFromDecode."""

    telegram_user_id: TelegramUserId
    token: str
    surface: UsageSurface


@dataclass(frozen=True, slots=True)
class SuggestRuleFromDecodeResult:
    """Result of SuggestRuleFromDecode."""

    outcome: SuggestRuleFromDecodeOutcome
    suggestion: RuleSuggestion | None = None


@dataclass(frozen=True, slots=True)
class SuggestRuleFromDecodePorts:
    """Injected collaborators for SuggestRuleFromDecode."""

    uow_factory: UnitOfWorkFactory
    catalog: ConsentCatalog
    rule_sources: RuleSources
    generator: TextGenerator
    quota: RateLimiter
    sink: UsageEventSink
    clock: Clock
    monotonic: MonotonicClock
    ids: IdGenerator
    pseudonymizer: Pseudonymizer
    crisis_screen: CrisisScreen
    deadline_seconds: float


@dataclass(frozen=True, slots=True)
class _PreparedCall:
    user_id: UserId
    payload: RuleSourcePayload
    relationship: RelationshipKind
    rules: tuple[RuleContext, ...]


class SuggestRuleFromDecode:
    """Redeem a sealed decode source and optionally create a decode RuleSuggestion."""

    def __init__(self, ports: SuggestRuleFromDecodePorts) -> None:
        self._ports = ports

    async def execute(self, command: SuggestRuleFromDecodeCommand) -> SuggestRuleFromDecodeResult:
        """Access → redeem → crisis → ownership → pending → quota → generate."""
        async with self._ports.uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if user is None:
                raise NotFound()
            await require_access(uow, self._ports.catalog, user.id)

        user_key = str(command.telegram_user_id.value)
        early = await self._redeem_and_screen(command.token, user_key)
        if isinstance(early, SuggestRuleFromDecodeResult):
            return early
        prepared = await self._load_for_generation(command.telegram_user_id, early)
        if isinstance(prepared, SuggestRuleFromDecodeResult):
            return prepared
        quota_pseudonym = self._ports.pseudonymizer.pseudonymize(_QUOTA_PURPOSE, user_key)
        if not (await self._ports.quota.check(quota_pseudonym)).allowed:
            return SuggestRuleFromDecodeResult(outcome=SuggestRuleFromDecodeOutcome.QUOTA_EXCEEDED)
        return await self._generate_and_store(
            user_key=user_key,
            surface=command.surface,
            prepared=prepared,
        )

    async def _redeem_and_screen(
        self, token: str, user_key: str
    ) -> RuleSourcePayload | SuggestRuleFromDecodeResult:
        source_pseudonym = self._ports.pseudonymizer.pseudonymize(RULE_SOURCE_PURPOSE, user_key)
        try:
            payload = await self._ports.rule_sources.redeem_once(source_pseudonym, token)
        except RuleSourceUnavailable:
            return SuggestRuleFromDecodeResult(outcome=SuggestRuleFromDecodeOutcome.UNAVAILABLE)
        if self._ports.crisis_screen.hit(payload.incoming_text):
            return SuggestRuleFromDecodeResult(outcome=SuggestRuleFromDecodeOutcome.CRISIS)
        return payload

    async def _load_for_generation(
        self,
        telegram_user_id: TelegramUserId,
        payload: RuleSourcePayload,
    ) -> _PreparedCall | SuggestRuleFromDecodeResult:
        async with self._ports.uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                raise NotFound()
            contact = await uow.contacts.get(payload.contact_id)
            if contact is None or contact.owner_id != user.id:
                raise NotFound()
            existing = await uow.rule_suggestions.list_pending_for_contact(
                user.id, payload.contact_id
            )
            decode_pending = next(
                (s for s in existing if s.source is SuggestionSource.DECODE),
                None,
            )
            if decode_pending is not None:
                return SuggestRuleFromDecodeResult(
                    outcome=SuggestRuleFromDecodeOutcome.PENDING_EXISTS,
                    suggestion=decode_pending,
                )
            owned, pair = await load_owned_contact(uow, user.id, contact)
            views = await collect_effective_rules(uow, owned, pair)
            rules = tuple(
                RuleContext(
                    category=view.category,
                    text=view.text.value,
                    effective_since=view.effective_since,
                )
                for view in views
            )
            return _PreparedCall(
                user_id=user.id,
                payload=payload,
                relationship=owned.relationship,
                rules=rules,
            )

    async def _generate_and_store(
        self, *, user_key: str, surface: UsageSurface, prepared: _PreparedCall
    ) -> SuggestRuleFromDecodeResult:
        started = self._ports.monotonic.monotonic()
        try:
            generated = await self._ports.generator.suggest_rule(
                SuggestRuleRequest(
                    incoming=prepared.payload.incoming_text,
                    rules=prepared.rules,
                    relationship=prepared.relationship,
                    deadline_seconds=self._ports.deadline_seconds,
                )
            )
        except (
            GenerationUnavailable,
            GenerationRefusedByProvider,
            InvalidGenerationOutput,
        ) as exc:
            await self._persist_error(
                user_key=user_key, surface=surface, started=started, error=exc
            )
            raise
        await self._persist_ok(user_key=user_key, surface=surface, result=generated)
        if isinstance(generated, SuggestRuleNothing):
            return SuggestRuleFromDecodeResult(outcome=SuggestRuleFromDecodeOutcome.NONE)
        return await self._persist_suggestion(prepared=prepared, proposed=generated)

    async def _persist_suggestion(
        self,
        *,
        prepared: _PreparedCall,
        proposed: SuggestRuleProposed,
    ) -> SuggestRuleFromDecodeResult:
        suggestion = RuleSuggestion.create_decode(
            suggestion_id=RuleSuggestionId(self._ports.ids.new_id()),
            user_id=prepared.user_id,
            contact_id=prepared.payload.contact_id,
            category=proposed.category,
            text=proposed.text,
            now=self._ports.clock.now(),
        )
        try:
            async with self._ports.uow_factory() as uow:
                await uow.rule_suggestions.add(suggestion)
                await uow.commit()
        except ConflictError:
            return await self._pending_after_conflict(prepared.user_id, prepared.payload.contact_id)
        return SuggestRuleFromDecodeResult(
            outcome=SuggestRuleFromDecodeOutcome.OK,
            suggestion=suggestion,
        )

    async def _pending_after_conflict(
        self, user_id: UserId, contact_id: ContactId
    ) -> SuggestRuleFromDecodeResult:
        async with self._ports.uow_factory() as uow:
            existing = await uow.rule_suggestions.list_pending_for_contact(user_id, contact_id)
            decode_pending = next(
                (s for s in existing if s.source is SuggestionSource.DECODE),
                None,
            )
            if decode_pending is None:
                raise NotFound()
            return SuggestRuleFromDecodeResult(
                outcome=SuggestRuleFromDecodeOutcome.PENDING_EXISTS,
                suggestion=decode_pending,
            )

    async def _persist_ok(
        self, *, user_key: str, surface: UsageSurface, result: SuggestRuleResult
    ) -> None:
        meta = result.meta
        analytics = self._ports.pseudonymizer.pseudonymize(_ANALYTICS_PURPOSE, user_key)
        event = UsageEvent(
            id=UsageEventId(self._ports.ids.new_id()),
            occurred_at=self._ports.clock.now().replace(microsecond=0),
            user_pseudonym=analytics,
            scenario=UsageScenario.SUGGEST_RULE,
            surface=surface,
            outcome=UsageOutcome.OK,
            unavailable_kind=None,
            safety=None,
            model=meta.model,
            prompt_version=meta.prompt_version,
            latency_ms=meta.latency_ms,
            ttfc_ms=None,
            attempts=meta.attempts,
            input_tokens=meta.usage.input,
            output_tokens=meta.usage.output,
            billable_tokens=meta.usage.billable,
        )
        with contextlib.suppress(UsageEventWriteFailed):
            await self._ports.sink.record(event)

    async def _persist_error(
        self,
        *,
        user_key: str,
        surface: UsageSurface,
        started: float,
        error: GenerationUnavailable | GenerationRefusedByProvider | InvalidGenerationOutput,
    ) -> None:
        ended = self._ports.monotonic.monotonic()
        measured = max(0, int((ended - started) * 1000))
        analytics = self._ports.pseudonymizer.pseudonymize(_ANALYTICS_PURPOSE, user_key)
        usage = error.usage
        if isinstance(error, GenerationUnavailable):
            outcome = UsageOutcome.UNAVAILABLE
            unavailable_kind = error.kind.value
        elif isinstance(error, GenerationRefusedByProvider):
            outcome = UsageOutcome.REFUSED
            unavailable_kind = None
        else:
            outcome = UsageOutcome.INVALID_OUTPUT
            unavailable_kind = None
        event = UsageEvent(
            id=UsageEventId(self._ports.ids.new_id()),
            occurred_at=self._ports.clock.now().replace(microsecond=0),
            user_pseudonym=analytics,
            scenario=UsageScenario.SUGGEST_RULE,
            surface=surface,
            outcome=outcome,
            unavailable_kind=unavailable_kind,
            safety=None,
            model=error.model,
            prompt_version=error.prompt_version,
            latency_ms=measured,
            ttfc_ms=None,
            attempts=error.attempts,
            input_tokens=usage.input,
            output_tokens=usage.output,
            billable_tokens=usage.billable,
        )
        with contextlib.suppress(UsageEventWriteFailed):
            await self._ports.sink.record(event)
