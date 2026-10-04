"""Port for per-user in-process inline result reuse (hit / join / miss)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from svoi_pravila.application.errors import ApplicationError
from svoi_pravila.application.inline_reuse_status import InlineReuseStatus
from svoi_pravila.application.ports.generation import AppliedRuleView, SafetyVerdict, Variant
from svoi_pravila.domain.enums import UsageScenario

__all__ = [
    "InlineResultReuse",
    "InlineReuseStatus",
    "InlineReuseValue",
    "ProduceInlineReuse",
    "ReuseFailed",
    "ReuseSucceeded",
]


@dataclass(frozen=True, slots=True)
class InlineReuseValue:
    """OK-cacheable (or transient non-OK) compose payload without generator meta."""

    scenario: UsageScenario
    variants: tuple[Variant, ...]
    safety: SafetyVerdict
    applied_rules: tuple[AppliedRuleView, ...]


@dataclass(frozen=True, slots=True)
class ReuseSucceeded:
    """Successful reuse resolution (hit, join, or miss with a value)."""

    status: InlineReuseStatus
    value: InlineReuseValue


@dataclass(frozen=True, slots=True)
class ReuseFailed:
    """Failed reuse resolution (join or miss with a typed produce error)."""

    status: InlineReuseStatus
    error: ApplicationError


InlineReuseResolution = ReuseSucceeded | ReuseFailed

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
        """Drop stored entries for ``user_key``; mark in-flight produces no-store."""
