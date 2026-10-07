"""Coerce metric label values to domain enums or fixed allowlists."""

from __future__ import annotations

from svoi_pravila.application.errors import (
    AnalyticsJobName,
    AnalyticsJobStatus,
    CacheErrorKind,
)
from svoi_pravila.domain.enums import LimitKind, UsageOutcome, UsageScenario, UsageSurface

NONE = "none"
OTHER = "other"

TOKEN_KINDS: frozenset[str] = frozenset({"input", "output", "billable"})
STATUS_CLASSES: frozenset[str] = frozenset({"2xx", "3xx", "4xx", "5xx"})
TELEGRAM_RESULTS: frozenset[str] = frozenset({"ok", "error"})
HTTP_METHODS: frozenset[str] = frozenset(
    {"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"}
)

# Models configured in .env.example / Settings defaults (plus test factory names).
KNOWN_MODELS: frozenset[str] = frozenset(
    {
        "GigaChat-2",
        "GigaChat-2-Pro",
        "GigaChat-3-Lightning",
        NONE,
        OTHER,
    }
)

KNOWN_PROMPT_VERSIONS: frozenset[str] = frozenset(
    {
        "soften@v3",
        "help_say@v4",
        "decode@v3",
        "decode_analysis@v1",
        "suggest_rule@v1",
        "decode_analysis@v1+decode@v3",
        NONE,
        OTHER,
    }
)

# aiogram Update content attributes used as update_type labels.
KNOWN_UPDATE_TYPES: frozenset[str] = frozenset(
    {
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "inline_query",
        "chosen_inline_result",
        "callback_query",
        "shipping_query",
        "pre_checkout_query",
        "poll",
        "poll_answer",
        "my_chat_member",
        "chat_member",
        "chat_join_request",
        "message_reaction",
        "message_reaction_count",
        "chat_boost",
        "removed_chat_boost",
        "business_connection",
        "business_message",
        "edited_business_message",
        "deleted_business_messages",
        "purchased_paid_media",
        OTHER,
    }
)

SCENARIOS: frozenset[str] = frozenset(m.value for m in UsageScenario)
SURFACES: frozenset[str] = frozenset(m.value for m in UsageSurface)
OUTCOMES: frozenset[str] = frozenset(m.value for m in UsageOutcome)
LIMIT_KINDS: frozenset[str] = frozenset(m.value for m in LimitKind) | {NONE}
CACHE_ERROR_KINDS: frozenset[str] = frozenset(m.value for m in CacheErrorKind)
ANALYTICS_JOBS: frozenset[str] = frozenset(m.value for m in AnalyticsJobName)
ANALYTICS_STATUSES: frozenset[str] = frozenset(m.value for m in AnalyticsJobStatus)
TERMINAL_ANALYTICS_STATUSES: frozenset[str] = frozenset(
    {
        AnalyticsJobStatus.SUCCEEDED.value,
        AnalyticsJobStatus.FAILED.value,
        AnalyticsJobStatus.SKIPPED_LOCKED.value,
    }
)


def scenario_label(value: UsageScenario | str) -> str:
    """Return a scenario enum value or ``other``."""
    raw = value.value if isinstance(value, UsageScenario) else value
    return raw if raw in SCENARIOS else OTHER


def surface_label(value: UsageSurface | str) -> str:
    """Return a surface enum value or ``other``."""
    raw = value.value if isinstance(value, UsageSurface) else value
    return raw if raw in SURFACES else OTHER


def outcome_label(value: UsageOutcome | str) -> str:
    """Return an outcome enum value or ``other``."""
    raw = value.value if isinstance(value, UsageOutcome) else value
    return raw if raw in OUTCOMES else OTHER


def limit_kind_label(value: LimitKind | str | None, *, outcome: UsageOutcome | str) -> str:
    """Return ``none`` unless the outcome is limited; then a LimitKind value."""
    outcome_raw = outcome.value if isinstance(outcome, UsageOutcome) else outcome
    if outcome_raw != UsageOutcome.LIMITED.value:
        return NONE
    if value is None:
        return NONE
    raw = value.value if isinstance(value, LimitKind) else value
    return raw if raw in LIMIT_KINDS else OTHER


def model_label(value: str | None) -> str:
    """Return a known model name, ``none`` when absent, or ``other``."""
    if value is None or value == "":
        return NONE
    return value if value in KNOWN_MODELS else OTHER


def prompt_version_label(value: str | None) -> str:
    """Return a known prompt version, ``none`` when absent, or ``other``."""
    if value is None or value == "":
        return NONE
    return value if value in KNOWN_PROMPT_VERSIONS else OTHER


def token_kind_label(value: str) -> str:
    """Return input|output|billable or ``other``."""
    return value if value in TOKEN_KINDS else OTHER


def cache_error_kind_label(value: CacheErrorKind | str) -> str:
    """Return a CacheErrorKind value or ``other``."""
    raw = value.value if isinstance(value, CacheErrorKind) else value
    return raw if raw in CACHE_ERROR_KINDS else OTHER


def analytics_job_label(value: AnalyticsJobName | str) -> str:
    """Return an AnalyticsJobName value or ``other``."""
    raw = value.value if isinstance(value, AnalyticsJobName) else value
    return raw if raw in ANALYTICS_JOBS else OTHER


def analytics_status_label(value: AnalyticsJobStatus | str) -> str:
    """Return an AnalyticsJobStatus value or ``other``."""
    raw = value.value if isinstance(value, AnalyticsJobStatus) else value
    return raw if raw in ANALYTICS_STATUSES else OTHER


def update_type_label(value: str | None) -> str:
    """Return a known Telegram update type or ``other``."""
    if value is None or value == "":
        return OTHER
    return value if value in KNOWN_UPDATE_TYPES else OTHER


def telegram_result_label(value: str) -> str:
    """Return ok|error or ``other``."""
    return value if value in TELEGRAM_RESULTS else OTHER


def http_method_label(value: str | None) -> str:
    """Return an uppercase HTTP method or ``other``."""
    if value is None or value == "":
        return OTHER
    upper = value.upper()
    return upper if upper in HTTP_METHODS else OTHER


_STATUS_CLASS_BY_HUNDRED: dict[int, str] = {
    2: "2xx",
    3: "3xx",
    4: "4xx",
}


def status_class_label(status_code: int) -> str:
    """Map an HTTP status code to 2xx/3xx/4xx/5xx."""
    return _STATUS_CLASS_BY_HUNDRED.get(status_code // 100, "5xx")


def route_label(route_template: str | None) -> str:
    """Return the matched route template or ``unmatched``."""
    if route_template is None or route_template in {"", "<unmatched>"}:
        return "unmatched"
    return route_template
