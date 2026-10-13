"""Port for counting rewrite quality defects (C0 only: kinds, rules, reasons — never text)."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.application.errors import InvalidOutputReason
from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule


class VariantPolicyMonitor(Protocol):
    """Records quality defects of produced variants; never blocks the response."""

    def record(self, kind: VariantKind, rule: PolicyRule) -> None:
        """Count one violation of ``rule`` by a ``kind`` variant."""

    def record_invalid(
        self,
        reason: InvalidOutputReason,
        slot: VariantKind | None,
        finish_reason: str | None,
    ) -> None:
        """Count one attempt whose output failed validation.

        ``slot`` is the defective rewrite slot when known; ``finish_reason`` is the provider's.
        """
