"""`CatchUpBus` concurrency: per-consumer workers pump in parallel, ordering holds.

Proves the invariants the concurrent supervisor must keep: within a consumer dispatch
stays seq-ordered; across consumers a slow consumer never head-of-line-blocks a fast
sibling; membership changes reconcile live workers; stop drains every worker; and
the optional bulkhead bounds simultaneous pumping.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from pylzt.types import Category

from eventus_fakes import FakeCursorStore, FakeDeadLetterStore, FakeEventLog, FakeLastSeenStore
from lzt_eventus.baseline.store import LastSeenBatch
from lzt_eventus.bus.catchup import CatchUpBus
from lzt_eventus.consumers.consumer import BaseConsumer, BaseSubscription
from lzt_eventus.events.base import AggregateId, DomainEvent, EventType

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Evt(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.NEW_LOT


def _make_bus(**kw: object) -> tuple[CatchUpBus, FakeEventLog]:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = CatchUpBus(
        log,
        FakeCursorStore(),
        FakeDeadLetterStore(),
        idle_poll=0.05,
        **kw,  # type: ignore[arg-type]
    )
    return bus, log


_seq = 0


async def _append(log: FakeEventLog, count: int) -> None:
    global _seq
    batch = LastSeenBatch(category=Category.STEAM, poll_epoch=0, upserts={}, drops=frozenset())
    events: list[DomainEvent] = []
    for _ in range(count):
        _seq += 1
        events.append(
            _Evt.build(
                aggregate_id=AggregateId(f"a{_seq}"),
                occurred_at=_NOW,
                content_hash=f"h{_seq}",
                poll_epoch=0,
            )
        )
    await log.append(events, batch)


class _Recorder(BaseConsumer):
    def __init__(self, name: str) -> None:
        self.name = name
        self.subscriptions = [BaseSubscription(event_types=frozenset({EventType.NEW_LOT}))]
        self.seqs: list[int] = []

    async def handle(self, event: DomainEvent) -> None:
        self.seqs.append(event.seq)


async def _run(bus: CatchUpBus) -> tuple[asyncio.Task[None], asyncio.Event]:
    stop = asyncio.Event()
    task = asyncio.create_task(bus.run(stop))
    return task, stop


async def _stop(task: asyncio.Task[None], stop: asyncio.Event) -> None:
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)


async def _until(predicate: object, limit: float = 2.0) -> None:
    async def _wait() -> None:
        while not predicate():  # type: ignore[operator]  # noqa: ASYNC110 — convergence poll, no Event to await
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_wait(), timeout=limit)


async def test_within_module_dispatch_stays_seq_ordered() -> None:
    bus, log = _make_bus()
    rec = _Recorder("r")
    bus.register(rec)
    task, stop = await _run(bus)
    try:
        await _append(log, 25)
        bus.notify()
        await _until(lambda: len(rec.seqs) == 25)
        assert rec.seqs == sorted(rec.seqs)  # strictly increasing, no reorder
        assert rec.seqs == list(range(rec.seqs[0], rec.seqs[0] + 25))  # gapless
    finally:
        await _stop(task, stop)


async def test_slow_module_does_not_block_fast_sibling() -> None:
    bus, log = _make_bus()
    gate = asyncio.Event()

    class _Slow(BaseConsumer):
        name = "slow"

        def __init__(self) -> None:
            self.subscriptions = [BaseSubscription(event_types=frozenset({EventType.NEW_LOT}))]
            self.handled = 0

        async def handle(self, event: DomainEvent) -> None:
            await gate.wait()  # stuck until the test releases it
            self.handled += 1

    fast = _Recorder("fast")
    slow = _Slow()
    bus.register(fast)
    bus.register(slow)
    task, stop = await _run(bus)
    try:
        await _append(log, 5)
        bus.notify()
        await _until(lambda: len(fast.seqs) == 5)  # fast drains fully...
        assert slow.handled == 0  # ...while slow is still parked on event #1
        gate.set()
        await _until(lambda: slow.handled == 5)
    finally:
        gate.set()
        await _stop(task, stop)


async def test_stop_drains_every_worker() -> None:
    bus, log = _make_bus()
    bus.register(_Recorder("a"))
    bus.register(_Recorder("b"))
    task, stop = await _run(bus)
    await _append(log, 3)
    bus.notify()
    await _stop(task, stop)
    assert bus._workers == {}  # all per-consumer workers drained on stop


async def test_module_added_under_run_is_reconciled() -> None:
    bus, log = _make_bus()
    task, stop = await _run(bus)
    try:
        late = _Recorder("late")
        bus.register(late)  # flips membership → supervisor spins up a worker
        await _append(log, 4)
        bus.notify()
        await _until(lambda: len(late.seqs) == 4)
        assert bus.unregister("late")
        await _until(lambda: "late" not in bus._workers)
    finally:
        await _stop(task, stop)


async def test_bulkhead_bounds_simultaneous_pumping() -> None:
    bus, log = _make_bus(max_concurrent_consumers=1)
    active = 0
    peak = 0

    class _Busy(BaseConsumer):
        def __init__(self, name: str) -> None:
            self.name = name
            self.subscriptions = [BaseSubscription(event_types=frozenset({EventType.NEW_LOT}))]
            self.done = 0

        async def handle(self, event: DomainEvent) -> None:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)  # widen the overlap window
            active -= 1
            self.done += 1

    a, b = _Busy("a"), _Busy("b")
    bus.register(a)
    bus.register(b)
    task, stop = await _run(bus)
    try:
        await _append(log, 3)
        bus.notify()
        await _until(lambda: a.done == 3 and b.done == 3)
        assert peak == 1  # the semaphore serialized the two workers
    finally:
        await _stop(task, stop)


async def test_pump_once_stays_sequential_and_deterministic() -> None:
    bus, log = _make_bus()
    rec = _Recorder("r")
    bus.register(rec)
    await _append(log, 6)
    dispatched = await bus.pump_once()
    assert dispatched == 6
    assert rec.seqs == sorted(rec.seqs)
    assert await bus.pump_once() == 0  # cursor committed; nothing left


@pytest.mark.parametrize("count", [0, 1])
async def test_notify_membership_triggers_reconcile(count: int) -> None:
    # A provider-driven membership change with no register() call still reconciles.
    consumers: list[BaseConsumer] = [_Recorder(f"m{i}") for i in range(count)]

    async def provider() -> list[BaseConsumer]:
        return list(consumers)

    bus, _ = _make_bus()
    bus._consumer_provider = provider  # provider path (webhook-sink style membership)
    task, stop = await _run(bus)
    try:
        consumers.append(_Recorder("added"))
        bus.notify_membership()
        await _until(lambda: "added" in bus._workers)
    finally:
        await _stop(task, stop)


class _FlakyConsumer(BaseConsumer):
    """Fails `handle()` the first `fail_times` calls, then succeeds."""

    def __init__(self, name: str, fail_times: int) -> None:
        self.name = name
        self.subscriptions = [BaseSubscription(event_types=frozenset({EventType.NEW_LOT}))]
        self._fail_times = fail_times
        self.calls = 0

    async def handle(self, event: DomainEvent) -> None:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("boom")


async def test_deliver_retries_then_succeeds_without_park() -> None:
    bus, log = _make_bus(max_handle_attempts=3, backoff_base=0.01, backoff_max=0.01)
    consumer = _FlakyConsumer("flaky", fail_times=1)
    bus.register(consumer)
    await _append(log, 1)
    dispatched = await bus.pump_once()
    assert dispatched == 1
    assert consumer.calls == 2  # failed once, succeeded on retry
    assert await bus._dlq.count() == 0


async def test_deliver_exhausts_attempts_and_parks() -> None:
    bus, log = _make_bus(max_handle_attempts=2, backoff_base=0.01, backoff_max=0.01)
    consumer = _FlakyConsumer("dead", fail_times=99)
    bus.register(consumer)
    await _append(log, 1)
    dispatched = await bus.pump_once()
    assert dispatched == 1
    assert consumer.calls == 2
    assert await bus._dlq.count() == 1


async def test_deliver_backoff_is_exponential_and_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    bus, log = _make_bus(max_handle_attempts=4, backoff_base=1.0, backoff_max=2.5)
    consumer = _FlakyConsumer("dead", fail_times=99)
    bus.register(consumer)
    await _append(log, 1)
    await bus.pump_once()
    assert delays == [1.0, 2.0, 2.5]  # base*2**0, base*2**1, capped at 2.5 (would be 4.0)
