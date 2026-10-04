"""Port for per-user in-process inline result reuse (hit / join / miss)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from svoi_pravila.application.errors import ApplicationError
from svoi_pravila.application.ports.generation import AppliedRuleView, SafetyVerdict, Variant
from svoi_pravila.domain.enums import UsageScenario


class InlineReuseStatus(StrEnum):
    """How a reuse lookup resolved for this waiter."""

    HIT = "hit"
    JOIN = "join"
    MISS = "miss"


@dataclass(frozen=True, slots=True)
class InlineReuseValue:
    """OK-cacheable (or transient non-OK) compose payload without generator meta."""

    scenario: UsageScenario
    variants: tuple[Variant, ...]
    safety: SafetyVerdict
    applied_rules: tuple[AppliedRuleView, ...]


@dataclass(frozen=True, slots=True)
class InlineReuseResolution:
    """Outcome of ``resolve``; the port never raises application errors."""

    status: InlineReuseStatus
    value: InlineReuseValue | None
    error: ApplicationError | None


ProduceInlineReuse = Callable[[], Awaitable[InlineReuseValue]]


class InlineResultReuse(Protocol):
    """Per-user process-memory reuse of identical inline generations."""

    async def resolve(
        self,
        key: str,
        user_key: str,
        produce: ProduceInlineReuse,
    ) -> InlineReuseResolution:
        """Return a hit, join an in-flight produce, or run ``produce`` on miss."""

    def forget(self, user_key: str) -> None:
        """Drop stored entries for ``user_key``; later completions must not store."""
