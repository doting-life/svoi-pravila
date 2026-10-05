"""HTTP exceptions and handlers for the mini-app API."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from svoi_pravila.api.miniapp.errors import MiniappErrorCode, error_body
from svoi_pravila.application.errors import (
    AccessNotGranted,
    ContactLimitReached,
    NotFound,
    OpenRuleLimitReached,
)
from svoi_pravila.domain.errors import InvalidTransitionError, InvalidValueError

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


def register_miniapp_exception_handlers(app: FastAPI) -> None:
    """Register JSON error handlers with Cache-Control: no-store."""

    @app.exception_handler(MiniappHttpError)
    async def _miniapp_http_error(_request: Request, exc: MiniappHttpError) -> JSONResponse:
        return _miniapp_json(exc.code, exc.status_code)

    @app.exception_handler(NotFound)
    async def _not_found(_request: Request, _exc: NotFound) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.NOT_FOUND, 404)

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

    @app.exception_handler(InvalidTransitionError)
    async def _invalid_transition(_request: Request, _exc: InvalidTransitionError) -> JSONResponse:
        return _miniapp_json(MiniappErrorCode.INVALID_TRANSITION, 409)

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
        return _miniapp_json(MiniappErrorCode.VALIDATION_ERROR, 422)
