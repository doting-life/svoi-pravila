"""Port for counting variant-policy violations (C0: kind and rule only, never text)."""

from __future__ import annotations

from typing import Protocol

from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule


class VariantPolicyMonitor(Protocol):
    """Records that a produced variant broke a policy rule; never blocks the response."""

    def record(self, kind: VariantKind, rule: PolicyRule) -> None:
        """Count one violation of ``rule`` by a ``kind`` variant."""
