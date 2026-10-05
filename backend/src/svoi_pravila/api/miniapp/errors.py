"""Typed error codes and Russian catalog for the mini-app API."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from svoi_pravila.api.miniapp.localization import load_ru_messages


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
    INVALID_TRANSITION = "invalid_transition"
    VALIDATION_ERROR = "validation_error"
    BODY_TOO_LARGE = "body_too_large"


class ErrorBody(BaseModel):
    """Shared JSON error envelope."""

    code: MiniappErrorCode
    message: str = Field(min_length=1)


def error_body(code: MiniappErrorCode) -> ErrorBody:
    """Build an error body with the catalog message (never echo input)."""
    message = load_ru_messages().get(code.value)
    if message is None:
        msg = f"missing mini-app message for {code.value}"
        raise KeyError(msg)
    return ErrorBody(code=code, message=message)
