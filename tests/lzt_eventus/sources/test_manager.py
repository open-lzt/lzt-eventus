"""`SourceManager` — restart-with-backoff on unexpected task completion + drain()."""

from __future__ import annotations

import asyncio

import pytest

from lzt_eventus.errors import DuplicateSource, SourceExhausted, SourceNotFound
from lzt_eventus.sources.base import BaseSource
from lzt_eventus.sources.manager import SourceManager


class _ImmediatelyFailingSource(BaseSource):
    """Simulates a BaseException slipping past BaseSource.run()'s except-Exception guard."""

    name = "boom"

    def __init__(self) -> None:
        super().__init__(min_cadence=0.01, max_cadence=0.02, cadence=0.01)
        self.starts = 0

    async def poll_once(self) -> int:
        return 0

    async def run(self, stop: asyncio.Event) -> None:
        self.starts += 1
        raise RuntimeError("boom")


class _SlowSource(BaseSource):
    name = "slow"

    def __init__(
        self, started: asyncio.Event, release: asyncio.Event, finished: asyncio.Event
    ) -> None:
        super().__init__(min_cadence=10.0, max_cadence=10.0, cadence=10.0)
        self._started = started
        self._release = release
        self._finished = finished

    async def poll_once(self) -> int:
        self._started.set()
        await self._release.wait()
        self._finished.set()
        return 0


async def test_restart_with_backoff_then_exhausted() -> None:
    source = _ImmediatelyFailingSource()
    manager = SourceManager(
        [source],
        max_restart_attempts=3,
        restart_backoff_base=0.001,
        restart_backoff_max=0.001,
    )

    with pytest.raises(SourceExhausted) as exc_info:
        await manager.supervise()

    assert source.starts == 4  # initial run + 3 restarts before exhaustion
    assert exc_info.value.name == "boom"
    assert exc_info.value.attempts == 4


async def test_drain_waits_for_in_flight_poll_once() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    source = _SlowSource(started, release, finished)
    manager = SourceManager([source])

    await manager._reconcile()  # start the source task directly (no supervise loop)
    await asyncio.wait_for(started.wait(), timeout=1.0)

    drain_task = asyncio.create_task(manager.drain())
    await asyncio.sleep(0)
    assert not drain_task.done()
    assert not finished.is_set()

    release.set()
    await asyncio.wait_for(drain_task, timeout=1.0)
    assert finished.is_set()


async def test_add_remove_source_errors_unchanged() -> None:
    manager = SourceManager()
    source = _ImmediatelyFailingSource()
    manager.add_source(source)

    with pytest.raises(DuplicateSource):
        manager.add_source(_ImmediatelyFailingSource())
    with pytest.raises(SourceNotFound):
        manager.remove_source("nope")

    manager.remove_source("boom")
    assert "boom" not in manager.source_names
