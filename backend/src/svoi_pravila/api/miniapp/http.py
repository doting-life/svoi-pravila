"""HTTP exceptions and handlers for the mini-app API."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from svoi_pravila.api.miniapp.errors import (
    MiniappErrorCode,
    error_body,
    limit_error_body,
    rule_text_too_long_body,
)
from svoi_pravila.application.errors import (
    AccessNotGranted,
    CacheUnavailable,
    ContactAlreadyLinked,
    ContactLimitReached,
    NotFound,
    OpenRuleLimitReached,
    RuleTextEmpty,
    RuleTextInvalidChars,
    RuleTextTooLong,
    ServiceBudgetExhausted,
    UserQuotaExhausted,
)
from svoi_pravila.domain.errors import (
    InvalidTransitionError,
    InvalidValueError,
    InviteAlreadyAcceptedError,
    InviteExpiredError,
    RuleTextEmptyError,
    RuleTextInvalidCharsError,
    RuleTextTooLongError,
    SelfInviteAcceptError,
)
from svoi_pravila.domain.text import RULE_TEXT_MAX_CHARS

_MINIAPP_PREFIX = "/api/v1"


class MiniappHttpError(Exception):
    """Carry a typed mini-app error to the exception handler."""

    def __init__(self, code: MiniappErrorCode, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code.value)


def map_access_error(exc: AccessNotGranted) -> MiniappHttpError:
    """Map AccessNotGranted to onboarding_required or consent_required."""
    if not exc.status.age_confirmed:
        return MiniappHttpError(MiniappErrorCode.ONBOARDING_REQUIRED, 403)
    if exc.status.missing_consents:
        return MiniappHttpError(MiniappErrorCode.CONSENT_REQUIRED, 403)
    return MiniappHttpError(MiniappErrorCode.ONBOARDING_REQUIRED, 403)


def _miniapp_json(code: MiniappErrorCode, status_code: int) -> JSONResponse:
    body = error_body(code)
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


def limit_json_response(
    code: MiniappErrorCode,
    *,
    status_code: int,
    resets_at: datetime,
    display_timezone: str,
) -> JSONResponse:
    """JSON limit error; ``Retry-After`` for service budget (seconds until reset)."""
    body = limit_error_body(code, resets_at=resets_at, display_timezone=display_timezone)
    headers: dict[str, str] = {"Cache-Control": "no-store"}
    if code is MiniappErrorCode.SERVICE_BUDGET_EXHAUSTED:
        now = datetime.now(tz=UTC)
        headers["Retry-After"] = str(max(0, int((resets_at - now).total_seconds())))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=headers,
    )


def register_miniapp_exception_handlers(app: FastAPI, *, display_timezone: str) -> None:
    """Register JSON error handlers with Cache-Control: no-store."""

    @app.exception_handler(MiniappHttpError)
    async def _miniapp_http_error(_request: Request, exc: MiniappHttpError) -> JSONResponse:
        return _miniapp_json(exc.code, exc.status_code)

    @app.exception_handler(UserQuotaExhausted)
    async def _user_quota(_request: Request, exc: UserQuotaExhausted) -> JSONResponse:
        return limit_json_response(
            MiniappErrorCode.QUOTA_EXHAUSTED,
            status_code=429,
            resets_at=exc.resets_at,
            display_timezone=display_timezone,
        )

    @app.exception_handler(ServiceBudgetExhausted)
    async def _service_budget(_request: Request, exc: ServiceBudgetExhausted) -> JSONResponse:
        return limit_json_response(
            MiniappErrorCode.SERVICE_BUDGET_EXHAUSTED,
            status_code=503,
            resets_at=exc.resets_at,
            display_timezone=display_timezone,
        )

    @app.exception_handler(NotFound)
    async def _not_found(_request: Request, _exc: NotFound) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.NOT_FOUND, 404)

    @app.exception_handler(CacheUnavailable)
    async def _cache_unavailable(_request: Request, _exc: CacheUnavailable) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.SERVICE_UNAVAILABLE, 503)

    @app.exception_handler(AccessNotGranted)
    async def _access_not_granted(_request: Request, exc: AccessNotGranted) -> JSONResponse:
        mapped = map_access_error(exc)
        return _miniapp_json(mapped.code, mapped.status_code)

    @app.exception_handler(ContactLimitReached)
    async def _contact_limit(_request: Request, _exc: ContactLimitReached) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.CONTACT_LIMIT, 409)

    @app.exception_handler(OpenRuleLimitReached)
    async def _open_rule_limit(_request: Request, _exc: OpenRuleLimitReached) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.OPEN_RULE_LIMIT, 409)

    @app.exception_handler(ContactAlreadyLinked)
    async def _contact_already_linked(
        _request: Request, _exc: ContactAlreadyLinked
    ) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.CONTACT_ALREADY_LINKED, 409)

    @app.exception_handler(InvalidTransitionError)
    async def _invalid_transition(_request: Request, _exc: InvalidTransitionError) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.INVALID_TRANSITION, 409)

    @app.exception_handler(InviteExpiredError)
    async def _invite_expired(_request: Request, _exc: InviteExpiredError) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.INVITE_EXPIRED, 409)

    @app.exception_handler(InviteAlreadyAcceptedError)
    async def _invite_already_accepted(
        _request: Request, _exc: InviteAlreadyAcceptedError
    ) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.INVITE_INVALID, 409)

    @app.exception_handler(SelfInviteAcceptError)
    async def _invite_own(_request: Request, _exc: SelfInviteAcceptError) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.INVITE_OWN, 409)

    @app.exception_handler(RuleTextEmptyError)
    async def _rule_text_empty_domain(_request: Request, _exc: RuleTextEmptyError) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.RULE_TEXT_EMPTY, 422)

    @app.exception_handler(RuleTextInvalidCharsError)
    async def _rule_text_invalid_chars_domain(
        _request: Request, _exc: RuleTextInvalidCharsError
    ) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.RULE_TEXT_INVALID_CHARS, 422)

    @app.exception_handler(RuleTextTooLongError)
    async def _rule_text_too_long_domain(
        _request: Request, exc: RuleTextTooLongError
    ) -> JSONResponse:
        body = rule_text_too_long_body(maximum=exc.max, actual=exc.actual)
        return JSONResponse(
            status_code=422,
            content=body.model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RuleTextEmpty)
    async def _rule_text_empty_app(_request: Request, _exc: RuleTextEmpty) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.RULE_TEXT_EMPTY, 422)

    @app.exception_handler(RuleTextInvalidChars)
    async def _rule_text_invalid_chars_app(
        _request: Request, _exc: RuleTextInvalidChars
    ) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.RULE_TEXT_INVALID_CHARS, 422)

    @app.exception_handler(RuleTextTooLong)
    async def _rule_text_too_long_app(_request: Request, exc: RuleTextTooLong) -> JSONResponse:
        body = rule_text_too_long_body(maximum=exc.max, actual=exc.actual)
        return JSONResponse(
            status_code=422,
            content=body.model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(InvalidValueError)
    async def _invalid_value(_request: Request, _exc: InvalidValueError) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.VALIDATION_ERROR, 422)

    @app.exception_handler(RequestValidationError)
    async def _miniapp_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if not request.url.path.startswith(_MINIAPP_PREFIX):
            return JSONResponse(
                status_code=422,
                content={"detail": exc.errors()},
            )
        for err in exc.errors():
            loc = err.get("loc", ())
            if err.get("type") == "string_too_long" and "text" in loc:
                ctx = err.get("ctx") or {}
                actual = ctx.get("input_length")
                if not isinstance(actual, int):
                    input_value = err.get("input")
                    actual = (
                        len(input_value)
                        if isinstance(input_value, str)
                        else RULE_TEXT_MAX_CHARS + 1
                    )
                maximum = ctx.get("max_length", RULE_TEXT_MAX_CHARS)
                if not isinstance(maximum, int):
                    maximum = RULE_TEXT_MAX_CHARS
                body = rule_text_too_long_body(maximum=maximum, actual=actual)
                return JSONResponse(
                    status_code=422,
                    content=body.model_dump(mode="json"),
                    headers={"Cache-Control": "no-store"},
                )
        return _miniapp_json(MiniappErrorCode.VALIDATION_ERROR, 422)
