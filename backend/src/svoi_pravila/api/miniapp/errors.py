"""Typed error codes and Russian catalog for the mini-app API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from svoi_pravila.api.miniapp.localization import load_ru_messages
from svoi_pravila.limits import load_limits_catalog
from svoi_pravila.limits.catalog import format_reset_hhmm


class MiniappErrorCode(StrEnum):
    """Stable C0 error codes returned to the mini-app client."""

    UNAUTHORIZED = "unauthorized"
    INIT_DATA_INVALID = "init_data_invalid"
    INIT_DATA_EXPIRED = "init_data_expired"
    RATE_LIMITED = "rate_limited"
    ONBOARDING_REQUIRED = "onboarding_required"
    CONSENT_REQUIRED = "consent_required"
    NOT_FOUND = "not_found"
    CONTACT_LIMIT = "contact_limit"
    OPEN_RULE_LIMIT = "open_rule_limit"
    BOT_CHAT_UNAVAILABLE = "bot_chat_unavailable"
    SERVICE_UNAVAILABLE = "service_unavailable"
    CONTACT_ALREADY_LINKED = "contact_already_linked"
    CONTACT_NOT_PAIRED = "contact_not_paired"
    INVALID_TRANSITION = "invalid_transition"
    VALIDATION_ERROR = "validation_error"
    BODY_TOO_LARGE = "body_too_large"
    TEXT_TOO_SHORT = "text_too_short"
    TEXT_TOO_LONG = "text_too_long"
    QUOTA_EXCEEDED = "quota_exceeded"
    QUOTA_EXHAUSTED = "quota_exhausted"
    SERVICE_BUDGET_EXHAUSTED = "service_budget_exhausted"
    GENERATION_UNAVAILABLE = "generation_unavailable"
    INVALID_OUTPUT = "invalid_output"
    BUSY = "busy"
    INVITE_INVALID = "invite_invalid"
    INVITE_EXPIRED = "invite_expired"
    INVITE_OWN = "invite_own"
    CONSENT_STALE = "consent_stale"


class ErrorBody(BaseModel):
    """Shared JSON error envelope."""

    code: MiniappErrorCode
    message: str = Field(min_length=1)


class LimitErrorBody(BaseModel):
    """Quota / service-budget exhaustion (ADR-0009) with retry metadata."""

    code: MiniappErrorCode
    message: str = Field(min_length=1)
    retry_at: datetime


def error_body(code: MiniappErrorCode) -> ErrorBody:
    """Build an error body with the catalog message (never echo input)."""
    message = load_ru_messages().get(code.value)
    if message is None:
        msg = f"missing mini-app message for {code.value}"
        raise KeyError(msg)
    return ErrorBody(code=code, message=message)


def limit_error_body(
    code: MiniappErrorCode,
    *,
    resets_at: datetime,
    display_timezone: str,
) -> LimitErrorBody:
    """Build a limit error using the shared limits catalog (not mini-app ru.json)."""
    reset = format_reset_hhmm(resets_at, display_timezone)
    catalog = load_limits_catalog()
    if code is MiniappErrorCode.QUOTA_EXHAUSTED:
        message = catalog.user_quota_message(reset)
    elif code is MiniappErrorCode.SERVICE_BUDGET_EXHAUSTED:
        message = catalog.service_budget_message(reset)
    else:
        msg = f"limit_error_body does not support {code.value}"
        raise ValueError(msg)
    return LimitErrorBody(code=code, message=message, retry_at=resets_at)
