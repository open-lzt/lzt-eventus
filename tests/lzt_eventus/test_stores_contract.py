"""Shared contract suite for the durable stores.

Runs against both the in-memory fakes (always, proving they are semantically
equivalent to Postgres) and real Postgres (CI-only — skipped per-fixture when
`LZT_DATABASE_URL` is unset). Pins the *semantics* the store must satisfy:
gapless seq, ordered reads, deterministic-id dedup, atomic append+baseline,
optimistic cursor commit, and durable DLQ park/drain.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from pylzt.types import Category, ItemId
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eventus_fakes import (
    FakeCursorStore,
    FakeDeadLetterStore,
    FakeEventLog,
    FakeLastSeenStore,
)
from lzt_eventus.baseline.store import (
    BaseLastSeenStore,
    BaselineStore,
    LastSeenBatch,
)
from lzt_eventus.bus.dlq import (
    BaseDeadLetterStore,
    DeadLetterStore,
)
from lzt_eventus.cursor.base import BaseCursorStore
from lzt_eventus.cursor.store import CursorStore
from lzt_eventus.diff.snapshot import BaselineEntry
from lzt_eventus.errors import CursorConflict
from lzt_eventus.events.base import AggregateId, DomainEvent, EventType, make_event_id
from lzt_eventus.log.base import BaseEventLog
from lzt_eventus.log.store import EventStore
from lzt_eventus.orm import build_async_sessionmaker

_PG_DSN = os.environ.get("LZT_DATABASE_URL")
_BACKENDS = ["fakes", "postgres"]

_FIXED_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _async_dsn(raw: str) -> str:
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


def _make_event(
    aggregate: str,
    content_hash: str,
    *,
    poll_epoch: int = 1,
    event_type: EventType = EventType.NEW_LOT,
) -> DomainEvent:
    aid = AggregateId(aggregate)
    return DomainEvent(
        event_id=make_event_id(aid, event_type, content_hash, poll_epoch),
        aggregate_id=aid,
        occurred_at=_FIXED_TS,
        content_hash=content_hash,
        _event_type=event_type,
    )


def _empty_batch(epoch: int = 0) -> LastSeenBatch:
    return LastSeenBatch(category=Category.STEAM, poll_epoch=epoch)


async def _truncate(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text(
                "TRUNCATE event_log, last_seen, poll_epoch, consumer_cursor, "
                "dead_letter RESTART IDENTITY CASCADE"
            )
        )


_LogPair = tuple[BaseEventLog, BaseLastSeenStore]


@pytest_asyncio.fixture(params=_BACKENDS, ids=_BACKENDS)
async def log_pair(request: pytest.FixtureRequest) -> AsyncIterator[_LogPair]:
    if request.param == "fakes":
        fake_last_seen = FakeLastSeenStore()
        yield FakeEventLog(fake_last_seen), fake_last_seen
        return
    if _PG_DSN is None:
        pytest.skip("LZT_DATABASE_URL unset — postgres branch runs in CI against real Postgres")
    sessionmaker = build_async_sessionmaker(_async_dsn(str(_PG_DSN)))
    pg_last_seen = BaselineStore(sessionmaker)
    await _truncate(sessionmaker)
    yield EventStore(sessionmaker, pg_last_seen), pg_last_seen
    await _truncate(sessionmaker)


@pytest_asyncio.fixture(params=_BACKENDS, ids=_BACKENDS)
async def cursor_store(request: pytest.FixtureRequest) -> AsyncIterator[BaseCursorStore]:
    if request.param == "fakes":
        yield FakeCursorStore()
        return
    if _PG_DSN is None:
        pytest.skip("LZT_DATABASE_URL unset — postgres branch runs in CI against real Postgres")
    sessionmaker = build_async_sessionmaker(_async_dsn(str(_PG_DSN)))
    await _truncate(sessionmaker)
    yield CursorStore(sessionmaker)
    await _truncate(sessionmaker)


@pytest_asyncio.fixture(params=_BACKENDS, ids=_BACKENDS)
async def dlq_store(request: pytest.FixtureRequest) -> AsyncIterator[BaseDeadLetterStore]:
    if request.param == "fakes":
        yield FakeDeadLetterStore()
        return
    if _PG_DSN is None:
        pytest.skip("LZT_DATABASE_URL unset — postgres branch runs in CI against real Postgres")
    sessionmaker = build_async_sessionmaker(_async_dsn(str(_PG_DSN)))
    await _truncate(sessionmaker)
    yield DeadLetterStore(sessionmaker)
    await _truncate(sessionmaker)


async def test_append_assigns_gapless_seq(log_pair: tuple[BaseEventLog, BaseLastSeenStore]) -> None:
    log, _ = log_pair
    events = [_make_event("a1", "h1"), _make_event("a2", "h2"), _make_event("a3", "h3")]
    last = await log.append(events, _empty_batch())
    assert last == 3
    assert await log.max_seq() == 3
    read = await log.read_after(0, 10)
    assert [e.seq for e in read] == [1, 2, 3]


async def test_read_after_is_ordered_and_bounded(
    log_pair: tuple[BaseEventLog, BaseLastSeenStore],
) -> None:
    log, _ = log_pair
    events = [_make_event("a1", "h1"), _make_event("a2", "h2"), _make_event("a3", "h3")]
    await log.append(events, _empty_batch())
    page = await log.read_after(1, 1)
    assert [e.seq for e in page] == [2]
    assert all(e.event_type is EventType.NEW_LOT for e in page)


async def test_deterministic_id_dedup_is_noop(
    log_pair: tuple[BaseEventLog, BaseLastSeenStore],
) -> None:
    log, _ = log_pair
    event = _make_event("a1", "h1")
    assert await log.append([event], _empty_batch()) == 1
    assert await log.append([event], _empty_batch()) == 1
    assert await log.max_seq() == 1


async def test_append_applies_baseline_atomically(
    log_pair: tuple[BaseEventLog, BaseLastSeenStore],
) -> None:
    log, last_seen = log_pair
    entry = BaselineEntry(price=Decimal("10.50"), content_hash="h1", miss_count=2)
    batch = LastSeenBatch(category=Category.STEAM, poll_epoch=7, upserts={ItemId(42): entry})
    await log.append([_make_event("a1", "h1")], batch)
    baseline = await last_seen.get_baseline(Category.STEAM)
    assert baseline[ItemId(42)].price == Decimal("10.50")
    assert baseline[ItemId(42)].miss_count == 2
    assert await last_seen.get_poll_epoch(Category.STEAM) == 7


async def test_cursor_commit_advances(cursor_store: BaseCursorStore) -> None:
    await cursor_store.commit("m", 5, 0)
    state = await cursor_store.get("m")
    assert state.last_seq == 5
    assert state.version == 1
    await cursor_store.commit("m", 9, 1)
    state = await cursor_store.get("m")
    assert state.last_seq == 9
    assert state.version == 2


async def test_cursor_stale_version_conflicts(cursor_store: BaseCursorStore) -> None:
    await cursor_store.commit("m", 5, 0)
    with pytest.raises(CursorConflict):
        await cursor_store.commit("m", 7, 0)


async def test_cursor_watermark_is_min(cursor_store: BaseCursorStore) -> None:
    assert await cursor_store.watermark() is None
    await cursor_store.commit("a", 10, 0)
    await cursor_store.commit("b", 3, 0)
    assert await cursor_store.watermark() == 3


async def test_dlq_park_list_drain(dlq_store: BaseDeadLetterStore) -> None:
    event = _make_event("a1", "h1").with_seq(7)
    await dlq_store.park("m", event, reason="boom")
    assert await dlq_store.count() == 1
    listed = await dlq_store.list_for("m")
    assert len(listed) == 1
    assert listed[0].seq == 7
    assert listed[0].event.event_id == event.event_id
    assert listed[0].reason == "boom"
    drained = await dlq_store.drain("m")
    assert len(drained) == 1
    assert drained[0].event.event_id == event.event_id
    assert await dlq_store.count() == 0
