"""The single error→HTTP mapping point (handlers raise; this maps).

Reads `status_code`/`code`/args off a typed `WebError`, attaches the `request_id`,
and hides internals on a 5xx. Handlers never build a response for a failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse

from lzt_eventus.web.base.errors import WebError

if TYPE_CHECKING:
    from fastapi import FastAPI

_log = structlog.get_logger("lzt_eventus.web")


def web_error_response(request: Request, exc: WebError) -> JSONResponse:
    """The one `WebError` → `JSONResponse` mapping — shared with pre-routing middleware.

    `install_error_handlers` below covers errors raised inside a route/dependency.
    Middleware that runs *before* routing (e.g. `LimitValidationMiddleware`) can't rely
    on `@app.exception_handler` — that hook only fires for exceptions the router's
    `ExceptionMiddleware` sees, which sits *inside* user middleware, not outside it.
    Such middleware calls this directly instead of raising, so both paths render the
    exact same envelope.
    """
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "detail": exc.fields, "request_id": request_id},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(WebError)
    async def _web_error(request: Request, exc: WebError) -> JSONResponse:
        return web_error_response(request, exc)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        _log.exception("unhandled_error", request_id=request_id)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "request_id": request_id},
        )
