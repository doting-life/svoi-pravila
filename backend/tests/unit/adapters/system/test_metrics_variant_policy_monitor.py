"""MetricsVariantPolicyMonitor counts defects with C0 labels only."""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from svoi_pravila.adapters.system.metrics_variant_policy_monitor import (
    MetricsVariantPolicyMonitor,
)
from svoi_pravila.application.errors import InvalidOutputReason
from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule


def _count(kind: str, rule: str) -> float:
    value = REGISTRY.get_sample_value(
        "sp_variant_policy_violations_total", {"kind": kind, "rule": rule}
    )
    return 0.0 if value is None else value


def _invalid_count(reason: str, slot: str, finish_reason: str) -> float:
    value = REGISTRY.get_sample_value(
        "sp_variant_invalid_output_total",
        {"reason": reason, "slot": slot, "finish_reason": finish_reason},
    )
    return 0.0 if value is None else value


@pytest.mark.unit
def test_record_increments_the_counter_for_kind_and_rule() -> None:
    before = _count("softer", "stop_phrase")
    other = _count("firmer", "stop_phrase")
    MetricsVariantPolicyMonitor().record(VariantKind.SOFTER, PolicyRule.STOP_PHRASE)
    assert _count("softer", "stop_phrase") == before + 1
    assert _count("firmer", "stop_phrase") == other


@pytest.mark.unit
@pytest.mark.parametrize("rule", list(PolicyRule))
def test_every_policy_rule_has_its_own_label_value(rule: PolicyRule) -> None:
    before = _count("firmer", rule.value)
    MetricsVariantPolicyMonitor().record(VariantKind.FIRMER, rule)
    assert _count("firmer", rule.value) == before + 1


@pytest.mark.unit
def test_record_invalid_labels_reason_slot_and_finish_reason() -> None:
    before = _invalid_count("empty_text", "softer", "stop")
    other = _invalid_count("empty_text", "check", "stop")
    MetricsVariantPolicyMonitor().record_invalid(
        InvalidOutputReason.EMPTY_TEXT, VariantKind.SOFTER, "stop"
    )
    assert _invalid_count("empty_text", "softer", "stop") == before + 1
    assert _invalid_count("empty_text", "check", "stop") == other


@pytest.mark.unit
def test_record_invalid_uses_none_and_other_for_absent_or_unknown_values() -> None:
    absent = _invalid_count("length", "none", "none")
    unknown = _invalid_count("length", "none", "other")
    monitor = MetricsVariantPolicyMonitor()
    monitor.record_invalid(InvalidOutputReason.LENGTH, None, None)
    monitor.record_invalid(InvalidOutputReason.LENGTH, None, "surprise")
    assert _invalid_count("length", "none", "none") == absent + 1
    assert _invalid_count("length", "none", "other") == unknown + 1


@pytest.mark.unit
def test_record_check_empty_increments_its_unlabelled_counter() -> None:
    before = REGISTRY.get_sample_value("sp_variant_check_empty_total") or 0.0
    MetricsVariantPolicyMonitor().record_check_empty()
    assert REGISTRY.get_sample_value("sp_variant_check_empty_total") == before + 1
