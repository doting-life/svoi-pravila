"""Decode an incoming private-chat message with streaming generation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from logging import getLogger
from typing import Protocol

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    IncomingTextTooShort,
    InvalidGenerationOutput,
    NotFound,
    ScenarioBusy,
    ScenarioQuotaExceeded,
)
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.concurrency import ConcurrencyGuard
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.generation import (
    AnalysisChunk,
    DecodeCompleted,
    DecodeEvent,
    DecodeRequest,
    RuleContext,
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
from svoi_pravila.application.use_cases._contact_access import load_owned_contact
from svoi_pravila.domain.enums import (
    RelationshipKind,
    RuleStatus,
    UsageOutcome,
    UsageScenario,
    UsageSurface,
)
from svoi_pravila.domain.ids import RuleId, TelegramUserId, UsageEventId
from svoi_pravila.domain.rules import ContactScope, PairScope
from svoi_pravila.domain.usage import UsageEvent

_LOG = getLogger(__name__)

_TEXT_MIN = 1
_TEXT_MAX = 4000
_LOCK_MARGIN_SECONDS = 5
_RATE_LIMIT_PURPOSE = "rate_limit"
_QUOTA_PURPOSE = "decode_quota"
_ANALYTICS_PURPOSE = "analytics"
_UNKNOWN_PROMPT = "unknown"


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
    deadline_seconds: float
    decode_model: str


class IncomingDecoder(Protocol):
    """Streaming decode use case as seen by the channel adapter."""

    def execute(self, command: DecodeIncomingCommand) -> AsyncIterator[DecodeEvent]:
        """Yield analysis chunks then a completed result, or raise a typed error."""
        ...


class DecodeIncoming:
    """Stream a decode for a fully onboarded user."""

    def __init__(self, ports: DecodeIncomingPorts) -> None:
        self._ports = ports

    async def execute(self, command: DecodeIncomingCommand) -> AsyncIterator[DecodeEvent]:
        """Yield analysis chunks then a completed result, or raise a typed error."""
        text = command.incoming_text
        if len(text) < _TEXT_MIN:
            raise IncomingTextTooShort()
        if len(text) > _TEXT_MAX:
            raise IncomingTextTooLong()

        relationship, rules = await self._load_context(command.telegram_user_id)
        user_key = str(command.telegram_user_id.value)
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
                if isinstance(event, AnalysisChunk) and first_chunk_at is None:
                    first_chunk_at = self._ports.monotonic.monotonic()
                if isinstance(event, DecodeCompleted):
                    await self._record_event(
                        user_key=user_key,
                        started=started,
                        first_chunk_at=first_chunk_at,
                        completed=event,
                    )
                yield event
        except (
            GenerationRefusedByProvider,
            InvalidGenerationOutput,
            GenerationUnavailable,
        ) as exc:
            await self._record_event(
                user_key=user_key,
                started=started,
                first_chunk_at=first_chunk_at,
                error=exc,
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
            if user.active_contact_id is None:
                return RelationshipKind.OTHER, ()
            contact = await uow.contacts.get(user.active_contact_id)
            contact, pair = await load_owned_contact(uow, user.id, contact)
            candidates = await uow.rules.list_for_scope(ContactScope(contact_id=contact.id))
            if pair is not None:
                candidates = [
                    *candidates,
                    *(await uow.rules.list_for_scope(PairScope(pair_id=pair.id))),
                ]
            views: list[tuple[RuleId, RuleContext]] = []
            for rule in candidates:
                if rule.status is not RuleStatus.ACTIVE:
                    continue
                effective = rule.effective_revision
                if effective is None or effective.effective_since is None:
                    continue
                views.append(
                    (
                        rule.id,
                        RuleContext(
                            category=rule.category,
                            text=effective.text.value,
                            effective_since=effective.effective_since,
                        ),
                    )
                )
            views.sort(key=lambda item: (item[1].effective_since, item[0]))
            return contact.relationship, tuple(ctx for _, ctx in views)

    async def _record_event(
        self,
        *,
        user_key: str,
        started: float,
        first_chunk_at: float | None,
        completed: DecodeCompleted | None = None,
        error: (
            GenerationRefusedByProvider | InvalidGenerationOutput | GenerationUnavailable | None
        ) = None,
    ) -> None:
        ended = self._ports.monotonic.monotonic()
        latency_ms = max(0, int((ended - started) * 1000))
        ttfc_ms = None if first_chunk_at is None else max(0, int((first_chunk_at - started) * 1000))
        occurred = self._ports.clock.now().replace(microsecond=0)
        analytics = self._ports.pseudonymizer.pseudonymize(_ANALYTICS_PURPOSE, user_key)
        outcome = UsageOutcome.OK
        unavailable_kind: str | None = None
        safety: str | None = None
        model = self._ports.decode_model
        prompt_version = _UNKNOWN_PROMPT
        attempts = 1
        usage = TokenUsage()
        if completed is not None:
            meta = completed.result.meta
            model = meta.model
            prompt_version = meta.prompt_version
            latency_ms = meta.latency_ms
            attempts = meta.attempts
            usage = meta.usage
            safety = completed.result.safety.value
        elif isinstance(error, GenerationUnavailable):
            outcome = UsageOutcome.UNAVAILABLE
            unavailable_kind = error.kind.value
            attempts = error.attempts
            usage = error.usage
        elif isinstance(error, GenerationRefusedByProvider):
            outcome = UsageOutcome.REFUSED
            attempts = error.attempts
            usage = error.usage
        else:
            if not isinstance(error, InvalidGenerationOutput):
                msg = "decode usage record requires a completed result or a typed generation error"
                raise TypeError(msg)
            outcome = UsageOutcome.INVALID_OUTPUT
            attempts = error.attempts
            usage = error.usage
        event = UsageEvent(
            id=UsageEventId(self._ports.ids.new_id()),
            occurred_at=occurred,
            user_pseudonym=analytics,
            scenario=UsageScenario.DECODE,
            surface=UsageSurface.DM,
            outcome=outcome,
            unavailable_kind=unavailable_kind,
            safety=safety,
            model=model,
            prompt_version=prompt_version,
            latency_ms=latency_ms,
            ttfc_ms=ttfc_ms,
            attempts=attempts,
            input_tokens=usage.input,
            output_tokens=usage.output,
            billable_tokens=usage.billable,
        )
        try:
            await self._ports.sink.record(event)
        except Exception:
            _LOG.info("usage_event_write_failed")
