from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pylzt.errors import NotFound
from pylzt.lib.clock import FakeClock
from pylzt.types import ItemId

from eventus_fakes import FakeEventLog, FakeLastSeenStore
from lzt_eventus.events.account import GuaranteeExpiring
from lzt_eventus.events.base import AggregateId, DomainEvent, EventType
from lzt_eventus.sources.guarantee import GuaranteeSeeder, GuaranteeWatcher
from lzt_eventus.transport import LogTransport

_ITEM_ID = 12345


@dataclass(slots=True)
class _FakeLot:
    item_id: ItemId
    guarantee: str


class FakeClient:
    """Stub `Client.market.get_lot` — only method `GuaranteeWatcher`/`GuaranteeSeeder` call."""

    def __init__(self, guarantee_end: datetime | None) -> None:
        self._guarantee_end = guarantee_end
        self.market = self

    async def get_lot(self, item_id: ItemId) -> _FakeLot:
        if self._guarantee_end is None:
            raise NotFound(item_id)
        return _FakeLot(item_id=item_id, guarantee=self._guarantee_end.isoformat())


class FakeBus:
    def __init__(self) -> None:
        self.notified = 0

    def notify(self) -> None:
        self.notified += 1


def _fake_purchase_event(item_id: int) -> DomainEvent:
    return DomainEvent(
        event_id=uuid.uuid4(),
        aggregate_id=AggregateId(str(item_id)),
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        _event_type=EventType.ITEM_PURCHASED,
    )


async def _seed(client: FakeClient, last_seen: FakeLastSeenStore) -> None:
    seeder = GuaranteeSeeder(client=client, last_seen=last_seen)  # type: ignore[arg-type]  # duck-typed fake, only get_lot() is used
    await seeder.handle(_fake_purchase_event(_ITEM_ID))


def _watcher(
    client: FakeClient,
    log: FakeEventLog,
    last_seen: FakeLastSeenStore,
    bus: FakeBus,
    clock: FakeClock,
) -> GuaranteeWatcher:
    return GuaranteeWatcher(
        client=client,  # type: ignore[arg-type]  # duck-typed fake, only get_lot() is used
        transport=LogTransport(log, on_committed=bus.notify),
        last_seen=last_seen,
        min_cadence=1.0,
        max_cadence=60.0,
        cadence=5.0,
        clock=clock,
    )


async def test_seed_from_item_purchased_then_fires_only_nearest_threshold() -> None:
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    guarantee_end = clock.now() + timedelta(minutes=30)
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()

    await _seed(FakeClient(guarantee_end), last_seen)

    watcher = _watcher(FakeClient(guarantee_end), log, last_seen, bus, clock)
    emitted = await watcher.poll_once()

    assert emitted == 1
    guarantee_events = [e for e in log._events if isinstance(e, GuaranteeExpiring)]
    assert len(guarantee_events) == 1
    assert guarantee_events[0].item_id == _ITEM_ID
    assert bus.notified == 1


async def test_second_poll_does_not_refire_same_threshold() -> None:
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    guarantee_end = clock.now() + timedelta(minutes=30)
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()

    await _seed(FakeClient(guarantee_end), last_seen)

    watcher = _watcher(FakeClient(guarantee_end), log, last_seen, bus, clock)
    first = await watcher.poll_once()
    second = await watcher.poll_once()

    assert first == 1
    assert second == 0
    guarantee_events = [e for e in log._events if isinstance(e, GuaranteeExpiring)]
    assert len(guarantee_events) == 1  # dedup: threshold fired once, not twice
