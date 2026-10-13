"""MetricsVariantPolicyMonitor counts violations with C0 labels only."""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from svoi_pravila.adapters.system.metrics_variant_policy_monitor import (
    MetricsVariantPolicyMonitor,
)
from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule


def _count(kind: str, rule: str) -> float:
    value = REGISTRY.get_sample_value(
        "sp_variant_policy_violations_total", {"kind": kind, "rule": rule}
    )
    return 0.0 if value is None else value


@pytest.mark.unit
def test_record_increments_the_counter_for_kind_and_rule() -> None:
    before = _count("softer", "stop_phrase")
    other = _count("firmer", "stop_phrase")
    MetricsVariantPolicyMonitor().record(VariantKind.SOFTER, PolicyRule.STOP_PHRASE)
    assert _count("softer", "stop_phrase") == before + 1
    assert _count("firmer", "stop_phrase") == other
