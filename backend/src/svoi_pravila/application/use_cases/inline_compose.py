"""Compose soften / help-say variants for an inline query (non-streaming)."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.errors import (
    IncomingTextTooLong,
    InlineComposeFailed,
    InlineProduceError,
    InlineQueryTooShort,
    NotFound,
)
from svoi_pravila.application.inline_reuse_key import InlineReuseKeyMaterial, inline_reuse_key
from svoi_pravila.application.inline_reuse_status import InlineReuseStatus
from svoi_pravila.application.inline_text import normalize_inline_text
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.generation import (
    BOUNDED_TEXT_MAX,
    AppliedRuleView,
    HelpSayIntent,
    RuleContext,
    SafetyVerdict,
    Variant,
)
from svoi_pravila.application.ports.inline_result_reuse import (
    InlineResultReuse,
    InlineReuseValue,
    ReuseFailed,
)
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._access import require_access
from svoi_pravila.application.use_cases._generation_context import load_active_contact_rule_context
from svoi_pravila.application.use_cases.compose_generation import (
    ComposeGeneration,
    ComposeProduceMaterial,
)
from svoi_pravila.domain.enums import RelationshipKind, UsageScenario, UsageSurface
from svoi_pravila.domain.ids import TelegramUserId


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
    compose: ComposeGeneration
    reuse: InlineResultReuse
    min_chars: int
    intent_prefixes: tuple[tuple[str, HelpSayIntent], ...]


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
        material = ComposeProduceMaterial(
            user_key=user_key,
            scenario=scenario,
            intent=intent,
            draft=draft,
            relationship=relationship,
            rules=rules,
            surface=UsageSurface.INLINE,
        )
        if self._ports.compose.is_crisis(draft):
            await self._ports.compose.record_screened(material)
            return InlineComposeResult(
                scenario=scenario,
                variants=(),
                safety=SafetyVerdict.CRISIS,
                applied_rules=(),
                reuse=None,
            )
        reuse_material = InlineReuseKeyMaterial(
            user_key=user_key,
            scenario=scenario,
            intent=intent,
            draft=draft,
            relationship=relationship,
            rules=rules,
        )
        key = inline_reuse_key(reuse_material)

        async def produce() -> InlineReuseValue | InlineProduceError:
            return await self._ports.compose.produce(material)

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

    async def _load_context(
        self, telegram_user_id: TelegramUserId
    ) -> tuple[RelationshipKind, tuple[RuleContext, ...]]:
        async with self._ports.uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                raise NotFound()
            await require_access(uow, self._ports.catalog, user.id)
            return await load_active_contact_rule_context(uow, user)


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
