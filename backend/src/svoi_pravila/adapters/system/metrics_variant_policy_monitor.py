"""VariantPolicyMonitor that counts violations in Prometheus (C0 labels only)."""

from __future__ import annotations

from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule
from svoi_pravila.observability.metrics import families
from svoi_pravila.observability.metrics.labels import policy_rule_label, variant_kind_label


class MetricsVariantPolicyMonitor:
    """Increment ``sp_variant_policy_violations_total{kind,rule}``; never logs text."""

    def record(self, kind: VariantKind, rule: PolicyRule) -> None:
        """Count one violation of ``rule`` by a ``kind`` variant."""
        families.VARIANT_POLICY_VIOLATIONS.labels(
            kind=variant_kind_label(kind), rule=policy_rule_label(rule)
        ).inc()
