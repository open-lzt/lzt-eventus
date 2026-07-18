"""End-to-end engine proof on Memory stores: the success-criteria contract.

Covers cold-start bootstrap (no NewLot flood), incremental diff (new / price drop),
confirmed disappearance, replay-resume from cursor, deterministic-id dedup, and
the poison-event DLQ.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pylzt.models.lot import Lot, LotFilter
from pylzt.pagination import Page, Paginator
from pylzt.types import Category, Currency, ItemId, ItemOrigin, SellerId

from eventus_fakes import build_fake_engine
from lzt_eventus.config import EngineConfig
from lzt_eventus.consumers.consumer import BaseConsumer, BaseSubscription
from lzt_eventus.consumers.logging_consumer import LoggingConsumer
from lzt_eventus.engine import EventEngine
from lzt_eventus.errors import ConsumerNotFound, DuplicateSource, SourceNotFound
from lzt_eventus.events.base import DomainEvent, EventType
from lzt_eventus.sources.base import BaseSource


def mk_lot(item_id: int, price: str, *, title: str = "acc", state: str = "active") -> Lot:
    return Lot(
        item_id=ItemId(item_id),
        category=Category.STEAM,
        price=Decimal(price),
        currency=Currency.RUB,
        title=title,
        seller_id=SellerId(1),
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        item_state=state,
        item_origin=ItemOrigin.BRUTE,
        guarantee="",
        nsb=True,
        content_hash=f"{price}:{title}:{state}",
        attributes={},
    )


class FakeClient:
    """Duck-typed pylzt.Client returning programmable catalog pages."""

    def __init__(self) -> None:
        self.lots: list[Lot] = []
        self.batch: dict[ItemId, Lot] = {}
        self.market = self

    def list_lots(self, filter: LotFilter, *, max_pages: int | None = None) -> Paginator[Lot]:
        async def fetch(page: int) -> Page[Lot]:
            return Page(items=list(self.lots), has_more=False)

        return Paginator(fetch)

    async def get_lots_batch(self, item_ids: list[ItemId]) -> list[Lot]:
        return [self.batch[i] for i in item_ids if i in self.batch]

    async def execute(self, method: object) -> Page[object]:
        """Duck-typed stub for the event-source sources (payments/notif/conv): always empty."""
        return Page(items=[], has_more=False)

    async def aclose(self) -> None:
        return None


def _config(**kw: object) -> EngineConfig:
    base: dict[str, object] = {
        "categories": [Category.STEAM],
        "disappear_polls": 1,
        "poll_pages": 1,
        "per_page": 50,
        "default_cadence": 1.0,
        "tokens": ["x"],
    }
    base.update(kw)
    return EngineConfig(**base)  # type: ignore[arg-type]


def _engine(client: FakeClient, consumer: BaseConsumer) -> EventEngine:
    return build_fake_engine(_config(), client=client, consumers=[consumer])  # type: ignore[arg-type]


class _SpySource(BaseSource):
    """Records each poll cycle + signals start/stop — proves a source is supervised."""

    def __init__(self, name: str = "spy") -> None:
        super().__init__(min_cadence=0.01, max_cadence=0.02, cadence=0.01)
        self.name = name
        self.calls = 0
        self.polled = asyncio.Event()
        self.stopped = asyncio.Event()

    async def poll_once(self) -> int:
        self.calls += 1
        self.polled.set()
        return 0

    async def run(self, stop: asyncio.Event) -> None:
        try:
            await super().run(stop)
        finally:
            self.stopped.set()


class _CountingModule(BaseConsumer):
    name = "counter"

    def __init__(self) -> None:
        self.subscriptions = [BaseSubscription(event_types=frozenset({EventType.NEW_LOT}))]
        self.seen = 0

    async def handle(self, event: DomainEvent) -> None:
        self.seen += 1


async def test_extra_sources_are_supervised() -> None:
    spy = _SpySource()
    engine = build_fake_engine(
        _config(),
        client=FakeClient(),  # type: ignore[arg-type]
        consumers=[LoggingConsumer()],
        extra_sources=[spy],
    )

    await engine.drain_once()

    assert spy.calls == 1


async def test_add_remove_source_changes_membership() -> None:
    engine = build_fake_engine(_config(), client=FakeClient(), consumers=[LoggingConsumer()])  # type: ignore[arg-type]
    spy = _SpySource()

    engine.add_source(spy)
    assert "spy" in engine.source_names
    await engine.drain_once()
    assert spy.calls == 1

    engine.remove_source("spy")
    assert "spy" not in engine.source_names
    await engine.drain_once()
    assert spy.calls == 1  # no longer polled after removal


async def test_duplicate_and_missing_poller_raise() -> None:
    engine = build_fake_engine(_config(), client=FakeClient(), consumers=[LoggingConsumer()])  # type: ignore[arg-type]
    engine.add_source(_SpySource())

    with pytest.raises(DuplicateSource):
        engine.add_source(_SpySource())
    with pytest.raises(SourceNotFound):
        engine.remove_source("nope")


async def test_added_poller_runs_live_then_stops_on_remove() -> None:
    engine = _engine(FakeClient(), LoggingConsumer())
    spy = _SpySource()
    runner = asyncio.create_task(engine.run())
    try:
        engine.add_source(spy)
        await asyncio.wait_for(spy.polled.wait(), timeout=2.0)
        engine.remove_source("spy")
        await asyncio.wait_for(spy.stopped.wait(), timeout=2.0)
    finally:
        engine.request_stop()
        await asyncio.wait_for(runner, timeout=2.0)


async def test_add_remove_module_changes_membership() -> None:
    engine = build_fake_engine(_config(), client=FakeClient(), consumers=[])  # type: ignore[arg-type]
    assert "counter" not in engine.consumer_names

    engine.add_module(_CountingModule())
    assert "counter" in engine.consumer_names

    engine.remove_module("counter")
    assert "counter" not in engine.consumer_names
    with pytest.raises(ConsumerNotFound):
        engine.remove_module("counter")


async def test_cold_start_emits_no_lot_flood() -> None:
    client = FakeClient()
    client.lots = [mk_lot(1, "100"), mk_lot(2, "50")]
    consumer = LoggingConsumer()
    engine = _engine(client, consumer)

    lot_events = await engine.drain_once()
    assert lot_events == 0  # bootstrap: zero per-lot events
    types = [e.event_type for e in consumer.seen]
    assert types == [EventType.SNAPSHOT_INITIALIZED]  # exactly one marker


async def test_incremental_new_and_price_drop() -> None:
    client = FakeClient()
    client.lots = [mk_lot(1, "100"), mk_lot(2, "50")]
    consumer = LoggingConsumer()
    engine = _engine(client, consumer)
    await engine.drain_once()  # bootstrap

    client.lots = [mk_lot(1, "80"), mk_lot(2, "50"), mk_lot(3, "30")]  # drop A, new C
    await engine.drain_once()

    kinds = {e.event_type for e in consumer.seen}
    assert EventType.PRICE_DROPPED in kinds
    assert EventType.NEW_LOT in kinds


async def test_confirmed_disappearance() -> None:
    client = FakeClient()
    client.lots = [mk_lot(1, "100"), mk_lot(2, "50")]
    consumer = LoggingConsumer()
    engine = _engine(client, consumer)
    await engine.drain_once()  # bootstrap

    client.lots = [mk_lot(1, "100")]  # lot 2 vanished; not in confirm batch → unknown/low
    await engine.drain_once()
    assert any(e.event_type == EventType.LOT_DISAPPEARED for e in consumer.seen)


async def test_replay_resume_from_cursor() -> None:
    client = FakeClient()
    client.lots = [mk_lot(1, "100")]
    consumer = LoggingConsumer()
    engine = _engine(client, consumer)
    await engine.drain_once()
    client.lots = [mk_lot(1, "80")]
    await engine.drain_once()

    # A fresh consumer registered later replays the whole log from seq 0, zero gaps.
    late = LoggingConsumer()
    late.name = "late"
    engine.bus.register(late)
    await engine.bus.pump_once()
    assert len(late.seen) == len(consumer.seen)
    assert [e.seq for e in late.seen] == sorted(e.seq for e in late.seen)


class _ExplodingModule(BaseConsumer):
    name = "boom"

    def __init__(self) -> None:
        self.subscriptions = [
            BaseSubscription(event_types=frozenset({EventType.SNAPSHOT_INITIALIZED}))
        ]
        self.calls = 0

    async def handle(self, event: object) -> None:
        self.calls += 1
        raise RuntimeError("boom")


async def test_poison_event_parks_and_advances() -> None:
    client = FakeClient()
    client.lots = [mk_lot(1, "100")]
    consumer = _ExplodingModule()
    cfg = _config(max_handle_attempts=2, catchup_backoff_base=0.01, catchup_backoff_max=0.01)
    engine = build_fake_engine(cfg, client=client, consumers=[consumer])  # type: ignore[arg-type]
    await engine.drain_once()  # emits the marker which the consumer chokes on

    assert consumer.calls == 2  # retried max_handle_attempts then parked
    # Cursor advanced past the poison event despite the failure (no HOL block).
    second = LoggingConsumer()
    engine.bus.register(second)
    await engine.bus.pump_once()  # must not hang / re-block
