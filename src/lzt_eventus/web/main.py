"""App factory — assemble middleware + routers over an `EngineHandle` (one engine)."""

from __future__ import annotations

from fastapi import FastAPI

from lzt_eventus.web.middlewares.errors import install_error_handlers
from lzt_eventus.web.middlewares.limits import LimitValidationMiddleware
from lzt_eventus.web.middlewares.request_id import RequestIdMiddleware
from lzt_eventus.web.routes import (
    events,
    inbound,
    meta,
    scalar,
    streams,
    subscriptions,
    token_accounts,
)
from lzt_eventus.web.shared.handle import EngineHandle


def build_app(handle: EngineHandle) -> FastAPI:
    docs_url = "/docs" if handle.config.web_docs_enabled else None
    app = FastAPI(
        title="lzt-core management API",
        version="0.1.0",
        docs_url=docs_url,
        redoc_url=None,
    )
    app.state.handle = handle
    # Added in reverse-wrap order: the LAST middleware added runs FIRST on a request, so
    # RequestIdMiddleware (added after) sets `request.state.request_id` before
    # LimitValidationMiddleware runs — the limit error's envelope carries a request_id.
    app.add_middleware(LimitValidationMiddleware, max_limit=handle.config.max_query_limit)
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(subscriptions.router)
    app.include_router(token_accounts.router)
    app.include_router(streams.router)
    app.include_router(events.router)
    app.include_router(inbound.router)
    app.include_router(meta.router)
    if handle.config.web_docs_enabled:
        app.include_router(scalar.router)
    return app
