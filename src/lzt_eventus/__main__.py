"""Daemon entrypoint: `python -m lzt_eventus <run|replay|redrive|prune>`.

`run` wires the SQLAlchemy stores, starts sources + catch-up bus under a TaskGroup,
and serves the FastAPI management/streaming API on the same process; SIGTERM drains
both. The other commands are the ops hooks the `scripts/*.sh` call.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal

import structlog

from lzt_eventus.config import EngineConfig
from lzt_eventus.consumers.logging_consumer import LoggingConsumer
from lzt_eventus.engine import EventEngine
from lzt_eventus.logging_setup import configure_logging

_log = structlog.get_logger("lzt_eventus.daemon")


async def _run(config: EngineConfig) -> None:
    import uvicorn
    from sqlalchemy import text

    from lzt_eventus.daemon.observability import PrometheusMetrics
    from lzt_eventus.web.main import build_app
    from lzt_eventus.web.repos.subscription_repo import PostgresSubscriptionRepo
    from lzt_eventus.web.shared.handle import EngineHandle

    engine, sessionmaker = EventEngine.build(config, consumers=[LoggingConsumer()])
    metrics = PrometheusMetrics()

    async def _ready() -> bool:
        try:
            async with sessionmaker() as session:
                await session.execute(text("SELECT 1"))
        except Exception:  # readiness probe reports, never raises
            return False
        return True

    # `EventEngine.build()` always wires a token_repo/secret_box/reconciler (Decision 2/4) —
    # the asserts document that invariant for mypy rather than re-deriving them here.
    assert engine.token_repo is not None
    assert engine.secret_box is not None
    assert engine.account_reconciler is not None
    handle = EngineHandle(
        config=config,
        subscriptions=PostgresSubscriptionRepo(sessionmaker),
        event_log=engine.stores.log,
        cursors=engine.stores.cursor,
        ready=_ready,
        token_accounts=engine.token_repo,
        secret_box=engine.secret_box,
        account_reconciler=engine.account_reconciler,
        render_metrics=metrics.render,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_app(handle),
            host=config.health_host,
            port=config.health_port,
            log_level="info",
        )
    )

    def _shutdown() -> None:
        _log.info("shutdown_signal")
        engine.request_stop()
        server.should_exit = True

    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):  # add_signal_handler is POSIX-only
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _shutdown)

    _log.info("engine_starting", run_id=config.run_id, port=config.health_port)
    async with asyncio.TaskGroup() as tg:
        tg.create_task(engine.run())
        tg.create_task(server.serve())


async def _replay(config: EngineConfig, consumer: str, from_seq: int) -> None:
    engine, _ = EventEngine.build(config, consumers=[])
    state = await engine.stores.cursor.get(consumer)
    await engine.stores.cursor.commit(consumer, from_seq, state.version)
    _log.info("cursor_reset", consumer=consumer, to_seq=from_seq)


async def _redrive(config: EngineConfig, consumer: str) -> None:
    engine, _ = EventEngine.build(config, consumers=[])
    parked = await engine.stores.dlq.drain(consumer)
    if not parked:
        _log.info("redrive_empty", consumer=consumer)
        return
    min_seq = min(d.seq for d in parked)
    state = await engine.stores.cursor.get(consumer)
    await engine.stores.cursor.commit(consumer, max(min_seq - 1, 0), state.version)
    _log.info("redrive_done", consumer=consumer, count=len(parked), rewound_to=min_seq - 1)


async def _prune(config: EngineConfig) -> None:
    from lzt_eventus.daemon.retention import PgRetentionPruner, RetentionWorker
    from lzt_eventus.orm.base import build_async_sessionmaker

    sessionmaker = build_async_sessionmaker(config.database_url)
    engine, _ = EventEngine.build(config, consumers=[])
    worker = RetentionWorker(
        engine.stores.cursor,
        PgRetentionPruner(sessionmaker),
        retention_months=config.retention_months,
    )
    pruned = await worker.run_once()
    _log.info("prune_done", rows=pruned)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lzt_eventus")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="run the engine daemon + management API")

    replay = sub.add_parser("replay", help="rewind a consumer cursor")
    replay.add_argument("--consumer", required=True)
    replay.add_argument("--from-seq", type=int, required=True)

    redrive = sub.add_parser("redrive", help="re-inject dead-lettered events")
    redrive.add_argument("--consumer", required=True)

    sub.add_parser("prune", help="run retention once (delete below watermark)")

    args = parser.parse_args(argv)
    configure_logging()
    config = EngineConfig()
    if args.command == "run":
        asyncio.run(_run(config))
    elif args.command == "replay":
        asyncio.run(_replay(config, args.consumer, args.from_seq))
    elif args.command == "redrive":
        asyncio.run(_redrive(config, args.consumer))
    elif args.command == "prune":
        asyncio.run(_prune(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
