"""VariantPolicyMonitor that counts defects in Prometheus (C0 labels only)."""

from __future__ import annotations

from svoi_pravila.application.errors import InvalidOutputReason
from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule
from svoi_pravila.observability.metrics import families
from svoi_pravila.observability.metrics.labels import (
    finish_reason_label,
    invalid_reason_label,
    policy_rule_label,
    repaired_defect_label,
    variant_kind_label,
    variant_slot_label,
)


class MetricsVariantPolicyMonitor:
    """Increment variant-quality counters; never logs text."""

    def record(self, kind: VariantKind, rule: PolicyRule) -> None:
        """Count one violation of ``rule`` by a ``kind`` variant."""
        families.VARIANT_POLICY_VIOLATIONS.labels(
            kind=variant_kind_label(kind), rule=policy_rule_label(rule)
        ).inc()

    def record_invalid(
        self,
        reason: InvalidOutputReason,
        slot: VariantKind | None,
        finish_reason: str | None,
    ) -> None:
        """Count one attempt that failed validation, by reason, slot and finish_reason."""
        families.VARIANT_INVALID_OUTPUT.labels(
            reason=invalid_reason_label(reason),
            slot=variant_slot_label(slot),
            finish_reason=finish_reason_label(finish_reason),
        ).inc()

    def record_repaired(self, defect: str) -> None:
        """Count one first-attempt defect repaired by the second attempt."""
        families.VARIANT_REPAIRED.labels(defect=repaired_defect_label(defect)).inc()

    def record_check_empty(self) -> None:
        """Count one answer whose ``check`` slot was empty."""
        families.VARIANT_CHECK_EMPTY.inc()
