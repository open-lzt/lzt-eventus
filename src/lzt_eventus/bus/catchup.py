"""`CatchUpBus` — pull-forward dispatch by cursor (not fire-and-forget pub/sub).

Each consumer has a `BaseCursorStore` cursor. On every wake the bus pulls
`log.read_after(cursor)` and dispatches in seq order; the consumer's cursor is
committed after each event (handled, skipped, or parked) so stop/restart resumes
from the committed seq — replayable, resumable, zero-loss. A `handle()` that fails
retries with exponential backoff (`backoff_base * 2**attempt`, capped at
`backoff_max`) and past `max_handle_attempts` is parked in the DLQ; the cursor
still advances, so one poison event can't head-of-line-block the consumer (A5/D24).

Concurrency model: `run()` is a supervisor that keeps ONE worker task per consumer
(`_workers`, keyed by `consumer.name`), each with its own stop + wake event. Consumers
are independent — separate cursors, separate DLQ keys, read-only log — so workers
pump concurrently without contention, and a slow consumer never head-of-line-blocks
a fast sibling. **Within a consumer dispatch stays strictly sequential** (one worker,
seq-ordered loop) — the cursor/ordering invariant must not be broken. `pump_once`
remains sequential and deterministic for `drain_once` / tests. An optional
`max_concurrent_consumers` semaphore bounds how many workers pump at once (bulkhead).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import structlog
from pylzt.lib.metrics import BaseMetrics, NullMetrics

from lzt_eventus.bus.dlq import BaseDeadLetterStore
from lzt_eventus.consumers.consumer import BaseConsumer
from lzt_eventus.cursor.base import BaseCursorStore
from lzt_eventus.events.base import DomainEvent
from lzt_eventus.log.base import BaseEventLog

_log = structlog.get_logger("lzt_eventus.bus")


@dataclass(slots=True)
class _Worker:
    task: asyncio.Task[None]
    stop: asyncio.Event
    wake: asyncio.Event


class CatchUpBus:
    def __init__(
        self,
        log: BaseEventLog,
        cursors: BaseCursorStore,
        dlq: BaseDeadLetterStore,
        *,
        max_handle_attempts: int = 5,
        read_limit: int = 500,
        idle_poll: float = 1.0,
        backoff_base: float = 1.0,
        backoff_max: float = 30.0,
        max_concurrent_consumers: int | None = None,
        metrics: BaseMetrics | None = None,
        consumer_provider: Callable[[], Awaitable[Sequence[BaseConsumer]]] | None = None,
    ) -> None:
        self._log = log
        self._cursors = cursors
        self._dlq = dlq
        self._max_attempts = max_handle_attempts
        self._read_limit = read_limit
        self._idle_poll = idle_poll
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._metrics = metrics or NullMetrics()
        self._consumers: list[BaseConsumer] = []
        # When set, the live consumer set is resolved per reconcile (dynamic membership —
        # webhook sinks whose subscriptions are created/removed at runtime).
        self._consumer_provider = consumer_provider
        # Bound on simultaneously-pumping workers; None = one slot per consumer.
        self._sem = (
            asyncio.Semaphore(max_concurrent_consumers) if max_concurrent_consumers else None
        )
        # Live per-consumer workers, keyed by consumer.name (the supervisor reconciles
        # this against the desired set). Membership changes flip _membership_changed.
        self._workers: dict[str, _Worker] = {}
        self._membership_changed = asyncio.Event()

    def register(self, consumer: BaseConsumer) -> None:
        self._consumers.append(consumer)
        self._membership_changed.set()

    def unregister(self, name: str) -> bool:
        """Drop a statically-registered consumer by its name. False if not present."""
        for i, consumer in enumerate(self._consumers):
            if consumer.name == name:
                del self._consumers[i]
                self._membership_changed.set()
                return True
        return False

    def consumer_names(self) -> tuple[str, ...]:
        """Names of the statically-registered consumers (excludes provider-supplied sinks)."""
        return tuple(m.name for m in self._consumers)

    def notify_membership(self) -> None:
        """Signal that a `consumer_provider`'s live set changed — triggers a reconcile."""
        self._membership_changed.set()

    async def _current_consumers(self) -> Sequence[BaseConsumer]:
        if self._consumer_provider is not None:
            return await self._consumer_provider()
        # Snapshot: add/remove may run between awaits inside a pump (D-DYNAMIC).
        return list(self._consumers)

    def notify(self) -> None:
        """Signal new events were appended — wakes every consumer worker promptly."""
        for worker in self._workers.values():
            worker.wake.set()

    async def pump_once(self) -> int:
        """One sequential pass over every consumer. Deterministic; for drain/tests."""
        dispatched = 0
        for consumer in await self._current_consumers():
            dispatched += await self._pump_consumer(consumer)
        return dispatched

    async def _pump_guarded(self, consumer: BaseConsumer, stop: asyncio.Event | None = None) -> int:
        if self._sem is None:
            return await self._pump_consumer(consumer, stop)
        async with self._sem:
            return await self._pump_consumer(consumer, stop)

    async def _pump_consumer(
        self, consumer: BaseConsumer, stop: asyncio.Event | None = None
    ) -> int:
        state = await self._cursors.get(consumer.name)
        last_seq, version = state.last_seq, state.version
        events = await self._log.read_after(last_seq, self._read_limit)
        handled = 0
        for event in events:
            if consumer.wants(event):
                await self._deliver(consumer, event, stop)
                handled += 1
            await self._cursors.commit(consumer.name, event.seq, version)
            version += 1
        return handled

    async def _deliver(
        self, consumer: BaseConsumer, event: DomainEvent, stop: asyncio.Event | None = None
    ) -> None:
        last_error = ""
        for attempt in range(self._max_attempts):
            try:
                await consumer.handle(event)
                return
            except Exception as exc:  # reliability boundary: poison parks, bus survives
                last_error = repr(exc)
                _log.warning(
                    "handle_failed",
                    consumer=consumer.name,
                    seq=event.seq,
                    attempt=attempt,
                    error=last_error,
                )
            if attempt < self._max_attempts - 1:
                await self._backoff_wait(attempt, stop)
        await self._dlq.park(consumer.name, event, reason=last_error)
        self._metrics.incr("dead_letter", consumer=consumer.name)
        _log.error("event_parked", consumer=consumer.name, seq=event.seq)

    async def _backoff_wait(self, attempt: int, stop: asyncio.Event | None) -> None:
        """Exponential delay before the next retry — capped, interruptible on shutdown.

        `stop` (the worker's stop event, when running under `run()`) shortens the wait
        so a shutdown doesn't stall behind a slow retry chain; the retry loop still runs
        its course either way, preserving the cursor-only-advances-after-park invariant.
        """
        backoff = min(self._backoff_base * (2**attempt), self._backoff_max)
        if stop is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=backoff)
        else:
            await asyncio.sleep(backoff)

    async def run(self, stop: asyncio.Event) -> None:
        """Supervise one worker per consumer; reconcile on membership change; drain on stop."""
        try:
            await self._reconcile_workers()
            while not stop.is_set():
                await self._await_membership_change(stop)
                self._membership_changed.clear()
                if stop.is_set():
                    break
                await self._reconcile_workers()
        finally:
            await self._drain_workers()

    async def _await_membership_change(self, stop: asyncio.Event) -> None:
        waiters = (
            asyncio.create_task(stop.wait()),
            asyncio.create_task(self._membership_changed.wait()),
        )
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter

    async def _reconcile_workers(self) -> None:
        desired = {m.name: m for m in await self._current_consumers()}
        for name, consumer in desired.items():
            if name not in self._workers:
                worker_stop = asyncio.Event()
                wake = asyncio.Event()
                task = asyncio.create_task(
                    self._run_worker(consumer, worker_stop, wake), name=f"bus:{name}"
                )
                task.add_done_callback(self._on_worker_done)
                self._workers[name] = _Worker(task=task, stop=worker_stop, wake=wake)
        for name in [n for n in self._workers if n not in desired]:
            await self._stop_worker(name)

    async def _run_worker(
        self, consumer: BaseConsumer, stop: asyncio.Event, wake: asyncio.Event
    ) -> None:
        while not stop.is_set():
            wake.clear()  # edge-trigger: clear BEFORE the read so a concurrent append is never lost
            pumped = await self._pump_guarded(consumer, stop)
            if pumped == 0:
                await self._wait_worker(stop, wake)

    async def _wait_worker(self, stop: asyncio.Event, wake: asyncio.Event) -> None:
        waiters = (asyncio.create_task(stop.wait()), asyncio.create_task(wake.wait()))
        try:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait(
                    waiters, timeout=self._idle_poll, return_when=asyncio.FIRST_COMPLETED
                )
        finally:
            for waiter in waiters:
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter

    def _on_worker_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:  # a worker died on something _deliver doesn't catch (store/log I/O)
            _log.error("bus_worker_died", task=task.get_name(), error=repr(exc))

    async def _stop_worker(self, name: str) -> None:
        worker = self._workers.pop(name)
        worker.stop.set()
        worker.wake.set()  # break it out of an idle wait at once
        with contextlib.suppress(asyncio.CancelledError):
            await worker.task

    async def _drain_workers(self) -> None:
        for worker in self._workers.values():
            worker.stop.set()
            worker.wake.set()
        for name in list(self._workers):
            with contextlib.suppress(asyncio.CancelledError):
                await self._workers[name].task
        self._workers.clear()
