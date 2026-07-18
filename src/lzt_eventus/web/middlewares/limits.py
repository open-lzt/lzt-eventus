"""Validate the `limit` query param before it reaches any route — one bound, one shape.

Runs at the ASGI-middleware layer, ahead of FastAPI routing/dependency resolution,
so every endpoint that happens to accept `?limit=` (`/events/pending`,
`/subscriptions/list`, any future one) gets the same `invalid_limit` /
`limit_too_large` error without redeclaring the bound per route.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from lzt_eventus.web.base.errors import InvalidLimit, LimitTooLarge, WebError
from lzt_eventus.web.middlewares.errors import web_error_response

_LIMIT_PARAM = "limit"
_MIN_LIMIT = 1


class LimitValidationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, max_limit: int) -> None:
        super().__init__(app)
        self._max_limit = max_limit

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        raw = request.query_params.get(_LIMIT_PARAM)
        if raw is not None:
            try:
                self._check(raw)
            except WebError as exc:
                # Raised straight from middleware (not a route/dependency), so it never
                # reaches `ExceptionMiddleware` — build the response ourselves, same shape.
                return web_error_response(request, exc)
        return await call_next(request)

    def _check(self, raw: str) -> None:
        try:
            limit = int(raw)
        except ValueError as exc:
            raise InvalidLimit(limit=raw) from exc
        if limit < _MIN_LIMIT:
            raise InvalidLimit(limit=limit, min_limit=_MIN_LIMIT)
        if limit > self._max_limit:
            raise LimitTooLarge(limit=limit, max_limit=self._max_limit)
