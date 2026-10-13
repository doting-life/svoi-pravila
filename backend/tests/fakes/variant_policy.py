"""In-memory VariantPolicyMonitor recording every violation."""

from __future__ import annotations

from svoi_pravila.application.errors import InvalidOutputReason
from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule


class RecordingVariantPolicyMonitor:
    """Collects violations and invalid attempts reported by the compose use cases."""

    def __init__(self) -> None:
        self.records: list[tuple[VariantKind, PolicyRule]] = []
        self.invalid: list[tuple[InvalidOutputReason, VariantKind | None, str | None]] = []

    def record(self, kind: VariantKind, rule: PolicyRule) -> None:
        self.records.append((kind, rule))

    def record_invalid(
        self,
        reason: InvalidOutputReason,
        slot: VariantKind | None,
        finish_reason: str | None,
    ) -> None:
        self.invalid.append((reason, slot, finish_reason))
