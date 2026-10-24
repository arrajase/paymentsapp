"""One error shape for the whole API (plan 7.2): a code and a safe message, never internal details.

    {"error": {"code": "LIMIT_EXCEEDED", "message": "...", "request_id": "..."}}

Details go only to the logs, linked by request_id.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from paymentsapp.core.logging import request_id_var

log = logging.getLogger(__name__)


class AppError(Exception):
    """Raise for any expected failure. `message` is shown to the client, so keep it free of internal details."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def error_body(code: str, message: str) -> dict[str, dict[str, str | None]]:
    return {"error": {"code": code, "message": message, "request_id": request_id_var.get()}}


_STATUS_TO_CODE = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    429: "RATE_LIMITED",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, "INTERNAL_ERROR" if exc.status_code >= 500 else "BAD_REQUEST")
        # Starlette's own messages ("Not Found") are safe; anything else raised as HTTPException is replaced.
        message = exc.detail if isinstance(exc.detail, str) and exc.status_code < 500 else "request failed"
        return JSONResponse(status_code=exc.status_code, content=error_body(code, message))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations and messages only; never echo the submitted values back.
        fields = [".".join(str(p) for p in e.get("loc", ()) if p != "body") for e in exc.errors()]
        message = "invalid request: " + ", ".join(f for f in fields if f) if any(fields) else "invalid request"
        return JSONResponse(status_code=422, content=error_body("VALIDATION_ERROR", message))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content=error_body("INTERNAL_ERROR", "something went wrong"))
