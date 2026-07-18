"""`lzt_eventus.log.batches` — reusable page-then-advance-cursor iterator."""

from __future__ import annotations

from datetime import UTC, datetime

from pylzt.types import Category

from eventus_fakes import FakeEventLog, FakeLastSeenStore
from lzt_eventus.baseline.store import LastSeenBatch
from lzt_eventus.events.base import AggregateId, DomainEvent, EventType, make_event_id
from lzt_eventus.log.batches import batches

_FIXED_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _event(n: int) -> DomainEvent:
    aid = AggregateId(f"item-{n}")
    return DomainEvent(
        event_id=make_event_id(aid, EventType.NEW_LOT, str(n), n),
        aggregate_id=aid,
        occurred_at=_FIXED_TS,
        content_hash=str(n),
        _event_type=EventType.NEW_LOT,
    )


async def test_batches_pages_through_the_whole_log() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    for n in range(1, 6):
        await log.append([_event(n)], LastSeenBatch(category=Category.STEAM, poll_epoch=n))

    pages = [page async for page in batches(log, after=0, limit=2)]

    assert [e.content_hash for page in pages for e in page] == ["1", "2", "3", "4", "5"]
    assert [len(page) for page in pages] == [2, 2, 1]


async def test_batches_resumes_from_a_mid_log_cursor() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    for n in range(1, 4):
        await log.append([_event(n)], LastSeenBatch(category=Category.STEAM, poll_epoch=n))
    after = (await log.read_after(0, 1))[0].seq  # seq of event "1"

    pages = [page async for page in batches(log, after=after, limit=10)]

    assert [e.content_hash for page in pages for e in page] == ["2", "3"]


async def test_batches_stops_on_empty_log() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)

    pages = [page async for page in batches(log, after=0, limit=10)]

    assert pages == []
