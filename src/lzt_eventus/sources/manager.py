"""`SourceManager` — owns the source fleet's lifecycle (start/restart/drain).

Extracted out of `EventEngine` (`~/.claude/rules/patterns.md` Module Discovery):
the membership-reconcile + graceful-drain logic already existed as
`EventEngine._reconcile_sources`/`_drain_source_tasks`; this class promotes it to
an independently testable unit and adds the one real gap that promotion exposed —
`_reconcile_sources` only ever reacted to `add_source`/`remove_source` calls, never
to a source *task* finishing on its own (e.g. an uncaught `BaseException` slipping
past `BaseSource.run()`'s `except Exception` guard). `supervise()` now watches task
completion directly via `asyncio.wait(..., return_when=FIRST_COMPLETED)` and
restarts an unexpectedly-finished source with exponential backoff, up to
`max_restart_attempts` — past that it raises `SourceExhausted` (surfaced to the
caller of `supervise()`, e.g. `EventEngine.run()`'s `TaskGroup`).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

import structlog

from lzt_eventus.errors import DuplicateSource, SourceExhausted, SourceNotFound
from lzt_eventus.lib.clock import Clock, RealClock
from lzt_eventus.sources.base import BaseSource

_log = structlog.get_logger("lzt_eventus.sources.manager")


class SourceManager:
    def __init__(
        self,
        sources: tuple[BaseSource, ...] | list[BaseSource] = (),
        *,
        max_restart_attempts: int = 5,
        restart_backoff_base: float = 1.0,
        restart_backoff_max: float = 30.0,
        clock: Clock | None = None,
    ) -> None:
        self._max_restart_attempts = max_restart_attempts
        self._restart_backoff_base = restart_backoff_base
        self._restart_backoff_max = restart_backoff_max
        self._clock = clock or RealClock()
        # Desired source set, keyed by name; live tasks live in _source_tasks.
        # add_sources/remove_source mutate the desired set; the supervisor reconciles.
        self._sources: dict[str, BaseSource] = {}
        for source in sources:
            self._register(source)
        self._source_tasks: dict[str, tuple[asyncio.Task[None], asyncio.Event]] = {}
        self._restart_attempts: dict[str, int] = {}
        self._sources_changed = asyncio.Event()
        self._stop = asyncio.Event()

    @property
    def source_names(self) -> tuple[str, ...]:
        return tuple(self._sources)

    @property
    def sources(self) -> tuple[BaseSource, ...]:
        return tuple(self._sources.values())

    def add_source(self, source: BaseSource) -> None:
        """Add a source at runtime; the supervisor starts it on the next reconcile."""
        self._register(source)
        self._restart_attempts.pop(source.name, None)
        self._sources_changed.set()

    def remove_source(self, name: str) -> None:
        """Drop a source at runtime; its task is stopped gracefully if running."""
        if name not in self._sources:
            raise SourceNotFound(name)
        del self._sources[name]
        self._restart_attempts.pop(name, None)
        self._sources_changed.set()

    def request_stop(self) -> None:
        self._stop.set()

    def update_source_cadence(
        self,
        name: str,
        *,
        min_cadence: float | None = None,
        max_cadence: float | None = None,
        cadence: float | None = None,
    ) -> None:
        """Retune a live source's cadence bounds without a restart (library-design Law 28)."""
        if name not in self._sources:
            raise SourceNotFound(name)
        self._sources[name].update_cadence(
            min_cadence=min_cadence, max_cadence=max_cadence, cadence=cadence
        )

    def source(
        self, *, name: str
    ) -> Callable[[Callable[[], BaseSource]], Callable[[], BaseSource]]:
        """Decorator sugar for consumer-added sources: builds and registers on decoration.

        ```python
        @manager.source(name="my-custom")
        def _build_my_source() -> BaseSource:
            return MySource(client=client, cadence=30.0, ...)
        ```
        Equivalent to `manager.add_source(_build_my_source())` — a thin, bound (per-instance,
        never a module-level registry) convenience over the existing `add_source` seam.
        """

        def deco(factory: Callable[[], BaseSource]) -> Callable[[], BaseSource]:
            self.add_source(factory())
            return factory

        return deco

    def _register(self, source: BaseSource) -> None:
        if source.name in self._sources:
            raise DuplicateSource(source.name)
        self._sources[source.name] = source

    async def supervise(self) -> None:
        """Reconcile live source tasks against the desired set until stop is set."""
        try:
            await self._reconcile()
            while not self._stop.is_set():
                await self._wait_for_event()
                if self._stop.is_set():
                    break
                self._sources_changed.clear()
                await self._reconcile()
        finally:
            await self.drain()

    async def drain(self) -> None:
        """Gracefully stop every live source: signal stop, await in-flight poll_once, clear.

        Independently callable (not only via `supervise()`'s `finally`) — waits for
        each source's current `poll_once()` to return before the task is joined.
        """
        for _task, stop in self._source_tasks.values():
            stop.set()
        for name in list(self._source_tasks):
            task, _stop = self._source_tasks[name]
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._source_tasks.clear()

    async def _wait_for_event(self) -> None:
        """Wake on: stop requested, membership changed, or a source task finishing."""
        stop_waiter = asyncio.create_task(self._stop.wait())
        changed_waiter = asyncio.create_task(self._sources_changed.wait())
        source_tasks = [task for task, _stop_evt in self._source_tasks.values()]
        waiters: list[asyncio.Task[object]] = [stop_waiter, changed_waiter, *source_tasks]
        try:
            done, _pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for control_waiter in (stop_waiter, changed_waiter):
                if not control_waiter.done():
                    control_waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await control_waiter

        if self._stop.is_set():
            return
        completed_names = [
            name for name, (task, _stop_evt) in list(self._source_tasks.items()) if task in done
        ]
        for name in completed_names:
            await self._handle_unexpected_completion(name)

    async def _handle_unexpected_completion(self, name: str) -> None:
        """A source task exited on its own (not via remove_source) — restart w/ backoff."""
        task, _stop_evt = self._source_tasks.pop(name)
        error = None if task.cancelled() else task.exception()
        source = self._sources.get(name)
        if source is None:
            return  # concurrently removed via remove_source — nothing to restart

        attempts = self._restart_attempts.get(name, 0) + 1
        self._restart_attempts[name] = attempts
        _log.error(
            "source_task_exited_unexpectedly",
            source=name,
            attempt=attempts,
            max_attempts=self._max_restart_attempts,
            error=repr(error) if error is not None else None,
        )
        if attempts > self._max_restart_attempts:
            raise SourceExhausted(name, attempts)

        backoff = min(self._restart_backoff_base * (2 ** (attempts - 1)), self._restart_backoff_max)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=backoff)
        if self._stop.is_set() or name not in self._sources:
            return
        self._start_task(name, self._sources[name])

    async def _reconcile(self) -> None:
        desired = dict(self._sources)
        for name, source in desired.items():
            if name not in self._source_tasks:
                self._start_task(name, source)
        for name in [n for n in self._source_tasks if n not in desired]:
            await self._stop_source_task(name)

    def _start_task(self, name: str, source: BaseSource) -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(source.run(stop), name=f"source:{name}")
        self._source_tasks[name] = (task, stop)

    async def _stop_source_task(self, name: str) -> None:
        task, stop = self._source_tasks.pop(name)
        stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await task
