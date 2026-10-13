"""Compose rewrite variants for an inline query (non-streaming, never a goal)."""

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
    reuse: InlineReuseStatus | None = None


@dataclass(frozen=True, slots=True)
class InlineComposePorts:
    """Injected collaborators for InlineCompose."""

    uow_factory: UnitOfWorkFactory
    catalog: ConsentCatalog
    compose: ComposeGeneration
    reuse: InlineResultReuse
    min_chars: int


class InlineCompose:
    """Produce inline rewrite variants for a fully onboarded user."""

    def __init__(self, ports: InlineComposePorts) -> None:
        self._ports = ports

    async def execute(self, command: InlineComposeCommand) -> InlineComposeResult:
        """Return variants or raise a typed error. Never streams."""
        user_key = str(command.telegram_user_id.value)
        await self._require_onboarded(command.telegram_user_id)
        normalized = normalize_inline_text(command.query)
        if len(normalized) > BOUNDED_TEXT_MAX:
            raise IncomingTextTooLong()
        if len(normalized) < self._ports.min_chars:
            raise InlineQueryTooShort()
        scenario = UsageScenario.REWRITE
        draft = normalized
        material = ComposeProduceMaterial(
            user_key=user_key,
            scenario=scenario,
            goal=None,
            draft=draft,
            relationship=RelationshipKind.OTHER,
            rules=(),
            surface=UsageSurface.INLINE,
        )
        if self._ports.compose.is_crisis(draft):
            await self._ports.compose.record_screened(material)
            return InlineComposeResult(
                scenario=scenario,
                variants=(),
                safety=SafetyVerdict.CRISIS,
                reuse=None,
            )
        reuse_material = InlineReuseKeyMaterial(
            user_key=user_key,
            scenario=scenario,
            draft=draft,
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
            reuse=resolution.status,
        )

    async def _require_onboarded(self, telegram_user_id: TelegramUserId) -> None:
        async with self._ports.uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                raise NotFound()
            await require_access(uow, self._ports.catalog, user.id)
