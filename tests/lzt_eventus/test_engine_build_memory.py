"""`EventEngine.build_memory()` — the embedded, zero-infra constructor (Law 29).

Proves the classmethod wires working in-memory stores end-to-end: a consumer
registered at construction time sees an event drained through the engine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import SecretStr
from pylzt.models.lot import Lot, LotFilter
from pylzt.pagination import Page, Paginator
from pylzt.types import Category, Currency, ItemId, ItemOrigin, SellerId

from lzt_eventus.config import EngineConfig
from lzt_eventus.consumers.logging_consumer import LoggingConsumer
from lzt_eventus.engine import EventEngine
from lzt_eventus.events.base import EventType


def _lot(item_id: int, price: str) -> Lot:
    return Lot(
        item_id=ItemId(item_id),
        category=Category.STEAM,
        price=Decimal(price),
        currency=Currency.RUB,
        title="acc",
        seller_id=SellerId(1),
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        item_state="active",
        item_origin=ItemOrigin.BRUTE,
        guarantee="",
        nsb=True,
        content_hash=price,
        attributes={},
    )


class FakeClient:
    """Duck-typed pylzt.Client returning a programmable catalog page."""

    def __init__(self) -> None:
        self.lots: list[Lot] = []
        self.market = self

    def list_lots(self, filter: LotFilter, *, max_pages: int | None = None) -> Paginator[Lot]:
        async def fetch(page: int) -> Page[Lot]:
            return Page(items=list(self.lots), has_more=False)

        return Paginator(fetch)

    async def get_lots_batch(self, item_ids: list[ItemId]) -> list[Lot]:
        return []

    async def execute(self, method: object) -> Page[object]:
        return Page(items=[], has_more=False)

    async def aclose(self) -> None:
        return None


def _config() -> EngineConfig:
    return EngineConfig(
        categories=[Category.STEAM],
        disappear_polls=1,
        poll_pages=1,
        per_page=50,
        default_cadence=1.0,
        tokens=[SecretStr("x")],
    )


async def test_build_memory_drains_events_through_registered_consumer() -> None:
    client = FakeClient()
    client.lots = [_lot(1, "100")]
    consumer = LoggingConsumer()

    engine = EventEngine.build_memory(
        client=client,  # type: ignore[arg-type]
        config=_config(),
        consumers=[consumer],
    )

    await engine.drain_once()  # cold-start bootstrap — no per-lot events yet
    assert [e.event_type for e in consumer.seen] == [EventType.SNAPSHOT_INITIALIZED]

    client.lots = [_lot(1, "80")]  # price drop
    await engine.drain_once()

    assert EventType.PRICE_DROPPED in {e.event_type for e in consumer.seen}


async def test_build_memory_defaults_config_when_omitted() -> None:
    engine = EventEngine.build_memory(client=FakeClient())  # type: ignore[arg-type]

    assert engine.consumer_names == ("guarantee_seeder",)  # auto-registered event source
    assert await engine.drain_once() == 0
