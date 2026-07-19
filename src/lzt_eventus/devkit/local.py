"""Embedded full-stack adapter — one call stands up a live engine + management API.

`local_eventus(...)` is the progressive-disclosure (library-design Law 30) quickstart
for the web/subscription side, the sibling of `EventEngine.build_memory()` on the
engine side. One `async with` gets a REAL live-polling in-memory engine, a FastAPI
management API served over the *same* stores the engine writes (Law 2: one engine),
and both running concurrently under a task group the caller owns — no signal handlers,
no owned event loop, cancellable in place.

Every seam underneath stays a constructor-injected override on
`EventEngine.build_memory()`: `client`, `config`, `consumers`, `extra_sources`, and
the in-memory stores / dedup cache. The advanced consumer who outgrows this drops
straight to `EventEngine.build_memory(...)` + `build_app(EngineHandle(...))` — the two
calls this function wraps — without forking anything.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import SecretStr
from pylzt.client import Client

from lzt_eventus.account.reconciler import AccountReconciler
from lzt_eventus.config import EngineConfig
from lzt_eventus.consumers.consumer import BaseConsumer
from lzt_eventus.engine import EventEngine
from lzt_eventus.sources.base import BaseSource
from lzt_eventus.web.repos.subscription_repo import MemorySubscriptionRepo
from lzt_eventus.web.repos.token_account_repo import MemoryTokenAccountRepo
from lzt_eventus.web.shared.handle import EngineHandle
from secret_box import SecretBox

if TYPE_CHECKING:
    import uvicorn

# Dev-only Fernet key: this adapter never touches Postgres and never persists tokens
# past the process, so a fixed in-memory key is safe. `build()` (the daemon) fails loud
# instead — a real deployment must set LZT_TOKEN_ENC_KEY.
_DEV_ENC_KEY = "devkit-in-memory-key-not-for-production-use"
_DEFAULT_ADMIN_KEY = "devkit-local-key"
_READY_POLL_SECONDS = 0.02
_START_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class LocalEventus:
    """A running local engine+API. Feed `base_url` + `api_key` to any management client."""

    base_url: str
    api_key: str
    engine: EventEngine


@asynccontextmanager
async def local_eventus(
    *,
    client: Client | None = None,
    tokens: Sequence[str] | None = None,
    config: EngineConfig | None = None,
    consumers: Sequence[BaseConsumer] = (),
    extra_sources: Sequence[BaseSource] = (),
    host: str = "127.0.0.1",
    port: int = 0,
    admin_api_key: str | None = None,
    log_level: str = "warning",
) -> AsyncIterator[LocalEventus]:
    """Run a live in-memory eventus engine + management API for the duration of the block.

    Pass either a ready `client` or `tokens` (a `Client` is built and closed for you).
    `config.categories` selects which catalogs the engine polls (default `[STEAM]`);
    `port=0` binds a free ephemeral port, reported back on `LocalEventus.base_url`.
    """
    if client is None:
        if not tokens:
            raise ValueError("local_eventus requires either `client` or `tokens`")
        client = Client(tokens=list(tokens))
        owns_client = True
    else:
        owns_client = False

    cfg = config or EngineConfig()
    key = admin_api_key or cfg.admin_api_key.get_secret_value() or _DEFAULT_ADMIN_KEY
    cfg = cfg.model_copy(update={"admin_api_key": SecretStr(key)})

    enc_key = cfg.token_enc_key.get_secret_value() or _DEV_ENC_KEY
    secret_box = SecretBox(enc_key)

    engine = EventEngine.build_memory(
        client=client,
        config=cfg,
        consumers=consumers,
        extra_sources=extra_sources,
    )

    token_accounts = MemoryTokenAccountRepo()
    reconciler = AccountReconciler(
        repo=token_accounts,
        engine=engine,
        secret_box=secret_box,
        min_cadence=cfg.min_cadence,
        max_cadence=cfg.max_cadence,
        cadence=cfg.rating_cadence,
    )

    async def _ready() -> bool:
        return True

    handle = EngineHandle(
        config=cfg,
        subscriptions=MemorySubscriptionRepo(),
        event_log=engine.stores.log,
        cursors=engine.stores.cursor,
        ready=_ready,
        token_accounts=token_accounts,
        secret_box=secret_box,
        account_reconciler=reconciler,
    )

    import uvicorn

    from lzt_eventus.web.main import build_app

    server = uvicorn.Server(
        uvicorn.Config(build_app(handle), host=host, port=port, log_level=log_level)
    )
    # Embedded: the host owns the process and its signals, not uvicorn.
    server.install_signal_handlers = lambda: None  # type: ignore[attr-defined]

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(engine.run())
            tg.create_task(server.serve())
            try:
                await _await_started(server)
                yield LocalEventus(
                    base_url=_resolve_base_url(server, host, port),
                    api_key=key,
                    engine=engine,
                )
            finally:
                engine.request_stop()
                server.should_exit = True
    finally:
        if owns_client:
            await client.aclose()


async def _await_started(server: uvicorn.Server) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _START_TIMEOUT_SECONDS
    while not server.started:
        if loop.time() >= deadline:
            raise RuntimeError(f"uvicorn did not start within {_START_TIMEOUT_SECONDS:.0f}s")
        await asyncio.sleep(_READY_POLL_SECONDS)


def _resolve_base_url(server: uvicorn.Server, host: str, requested_port: int) -> str:
    port = requested_port
    if port == 0 and server.servers:
        port = server.servers[0].sockets[0].getsockname()[1]
    display_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    return f"http://{display_host}:{port}"
