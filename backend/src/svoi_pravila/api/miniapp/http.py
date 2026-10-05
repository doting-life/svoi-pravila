"""HTTP exceptions and handlers for the mini-app API."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from svoi_pravila.api.miniapp.errors import MiniappErrorCode, error_body

_MINIAPP_PREFIX = "/api/v1"


class MiniappHttpError(Exception):
    """Carry a typed mini-app error to the exception handler."""

    def __init__(self, code: MiniappErrorCode, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code.value)


def register_miniapp_exception_handlers(app: FastAPI) -> None:
    """Register JSON error handlers with Cache-Control: no-store."""

    @app.exception_handler(MiniappHttpError)
    async def _miniapp_http_error(_request: Request, exc: MiniappHttpError) -> JSONResponse:
        body = error_body(exc.code)
        return JSONResponse(
            status_code=exc.status_code,
            content=body.model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def _miniapp_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if not request.url.path.startswith(_MINIAPP_PREFIX):
            return JSONResponse(
                status_code=422,
                content={"detail": exc.errors()},
            )
        body = error_body(MiniappErrorCode.VALIDATION_ERROR)
        return JSONResponse(
            status_code=422,
            content=body.model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )
