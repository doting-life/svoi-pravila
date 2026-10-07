"""Label coercion stays within domain enums and fixed allowlists."""

from __future__ import annotations

import pytest

from svoi_pravila.application.errors import (
    AnalyticsJobName,
    AnalyticsJobStatus,
    CacheErrorKind,
)
from svoi_pravila.domain.enums import LimitKind, UsageOutcome, UsageScenario, UsageSurface
from svoi_pravila.observability.metrics import labels


@pytest.mark.unit
def test_scenario_surface_outcome_limit_kind_cover_enums() -> None:
    for scenario in UsageScenario:
        assert labels.scenario_label(scenario) == scenario.value
        assert labels.scenario_label(scenario.value) == scenario.value
    assert labels.scenario_label("free-text") == labels.OTHER

    for surface in UsageSurface:
        assert labels.surface_label(surface) == surface.value
    assert labels.surface_label("chat") == labels.OTHER

    for outcome in UsageOutcome:
        assert labels.outcome_label(outcome) == outcome.value
    assert labels.outcome_label("weird") == labels.OTHER

    for kind in LimitKind:
        assert labels.limit_kind_label(kind, outcome=UsageOutcome.LIMITED) == kind.value
    assert labels.limit_kind_label(LimitKind.USER_QUOTA, outcome=UsageOutcome.OK) == labels.NONE
    assert labels.limit_kind_label(None, outcome=UsageOutcome.LIMITED) == labels.NONE
    assert labels.limit_kind_label("bogus", outcome=UsageOutcome.LIMITED) == labels.OTHER


@pytest.mark.unit
def test_model_and_prompt_version_allowlists() -> None:
    assert labels.model_label(None) == labels.NONE
    assert labels.model_label("") == labels.NONE
    assert labels.model_label("GigaChat-2-Pro") == "GigaChat-2-Pro"
    assert labels.model_label("secret-model") == labels.OTHER

    assert labels.prompt_version_label(None) == labels.NONE
    assert labels.prompt_version_label("decode@v3") == "decode@v3"
    assert labels.prompt_version_label("decode_analysis@v1+decode@v3") == (
        "decode_analysis@v1+decode@v3"
    )
    assert labels.prompt_version_label("decode@v99") == labels.OTHER


@pytest.mark.unit
def test_cache_analytics_telegram_http_labels() -> None:
    for kind in CacheErrorKind:
        assert labels.cache_error_kind_label(kind) == kind.value
    assert labels.cache_error_kind_label("x") == labels.OTHER

    for job in AnalyticsJobName:
        assert labels.analytics_job_label(job) == job.value
    for status in AnalyticsJobStatus:
        assert labels.analytics_status_label(status) == status.value

    assert labels.update_type_label("message") == "message"
    assert labels.update_type_label("not-a-type") == labels.OTHER
    assert labels.telegram_result_label("ok") == "ok"
    assert labels.telegram_result_label("fail") == labels.OTHER

    assert labels.http_method_label("get") == "GET"
    assert labels.http_method_label("TRACE") == labels.OTHER
    assert labels.status_class_label(201) == "2xx"
    assert labels.status_class_label(404) == "4xx"
    assert labels.status_class_label(500) == "5xx"
    assert labels.route_label("/healthz") == "/healthz"
    assert labels.route_label(None) == "unmatched"
    assert labels.route_label("<unmatched>") == "unmatched"


@pytest.mark.unit
def test_no_other_enum_values_can_be_produced_for_core_labels() -> None:
    """Every enum member maps to itself; nothing else maps into the enum sets."""
    assert {labels.scenario_label(m) for m in UsageScenario} == labels.SCENARIOS
    assert {labels.surface_label(m) for m in UsageSurface} == labels.SURFACES
    assert {labels.outcome_label(m) for m in UsageOutcome} == labels.OUTCOMES
    limited = {labels.limit_kind_label(m, outcome=UsageOutcome.LIMITED) for m in LimitKind}
    assert limited == {m.value for m in LimitKind}
    assert labels.limit_kind_label("user_quota", outcome=UsageOutcome.OK) == labels.NONE
