"""Registered ``sp_*`` Prometheus metric families."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from svoi_pravila.observability.metrics.buckets import (
    EVENT_LOOP_LAG_BUCKETS_SECONDS,
    HTTP_DURATION_BUCKETS_SECONDS,
    LLM_DURATION_BUCKETS_SECONDS,
)

LLM_REQUEST_DURATION = Histogram(
    "sp_llm_request_duration_seconds",
    "LLM / usage-event wall duration in seconds",
    ("scenario", "surface", "outcome", "model", "prompt_version"),
    buckets=LLM_DURATION_BUCKETS_SECONDS,
)

LLM_TTFC = Histogram(
    "sp_llm_ttfc_seconds",
    "Time to first decode chunk in seconds",
    ("scenario", "surface"),
    buckets=LLM_DURATION_BUCKETS_SECONDS,
)

LLM_TOKENS = Counter(
    "sp_llm_tokens_total",
    "LLM token counts from usage events",
    ("scenario", "kind"),
)

USAGE_EVENTS = Counter(
    "sp_usage_events_total",
    "Persisted usage events",
    ("scenario", "surface", "outcome", "limit_kind"),
)

LLM_BUDGET_SPENT = Gauge(
    "sp_llm_budget_spent_tokens",
    "Billable tokens spent for the current product day",
)

LLM_BUDGET_TOKENS = Gauge(
    "sp_llm_budget_tokens",
    "Configured daily LLM token budget",
)

HTTP_REQUEST_DURATION = Histogram(
    "sp_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ("route", "method", "status_class"),
    buckets=HTTP_DURATION_BUCKETS_SECONDS,
)

TELEGRAM_UPDATES = Counter(
    "sp_telegram_updates_total",
    "Telegram updates handled",
    ("update_type", "result"),
)

TELEGRAM_UPDATE_DURATION = Histogram(
    "sp_telegram_update_duration_seconds",
    "Telegram update handling duration in seconds",
    ("update_type",),
    buckets=HTTP_DURATION_BUCKETS_SECONDS,
)

CACHE_ERRORS = Counter(
    "sp_cache_errors_total",
    "Valkey errors mapped to CacheUnavailable",
    ("kind",),
)

ANALYTICS_JOB_RUNS = Counter(
    "sp_analytics_job_runs_total",
    "Analytics job_runs terminal statuses",
    ("job", "status"),
)

EVENT_LOOP_LAG = Histogram(
    "sp_event_loop_lag_seconds",
    "Event-loop sleep overshoot in seconds",
    buckets=EVENT_LOOP_LAG_BUCKETS_SECONDS,
)
