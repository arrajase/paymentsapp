"""Request ID and access logging. The request ID ties a client-visible error to its log lines."""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from paymentsapp.core.errors import error_body
from paymentsapp.core.logging import request_id_var

log = logging.getLogger("paymentsapp.access")

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller's ID only if it looks like one, so headers can't inject text into logs.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Handled here rather than by an exception handler, which would run outside this middleware and
                # lose the request ID that links the client's error to these log lines.
                log.exception("unhandled error", extra={"method": request.method, "path": request.url.path})
                response = JSONResponse(status_code=500, content=error_body("INTERNAL_ERROR", "something went wrong"))
            response.headers[REQUEST_ID_HEADER] = request_id
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            return response
        finally:
            request_id_var.reset(token)
