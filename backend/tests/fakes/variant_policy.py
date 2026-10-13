"""In-memory VariantPolicyMonitor recording every violation."""

from __future__ import annotations

from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule


class RecordingVariantPolicyMonitor:
    """Collects (kind, rule) pairs reported by the compose use cases."""

    def __init__(self) -> None:
        self.records: list[tuple[VariantKind, PolicyRule]] = []

    def record(self, kind: VariantKind, rule: PolicyRule) -> None:
        self.records.append((kind, rule))
