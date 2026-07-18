"""`EventStore` — the durable append-only log over SQLAlchemy async ORM.

`append` runs in ONE transaction: it inserts the events (deterministic `event_id`
→ `on_conflict_do_nothing` for crash-replay idempotency) AND applies the
`LastSeenBatch` through the *same* session, so events + baseline commit atomically
(no cross-store transaction with Redis). All appends are serialized through one
`asyncio.Lock` so the committed `seq` is gapless for readers — a consumer at
`cursor=N` never skips an in-flight lower `seq`. If concurrent appends were ever
allowed, `read_after` would instead have to clamp to the `pg_snapshot_xmin`
high-water mark; the single-funnel lock is the cheaper default.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lzt_eventus.baseline.store import BaselineStore, LastSeenBatch
from lzt_eventus.codecs.json import decode_event, encode_event
from lzt_eventus.events.base import DomainEvent
from lzt_eventus.log.base import BaseEventLog
from lzt_eventus.orm.base import build_async_sessionmaker
from lzt_eventus.orm.event_log import EventLog


class EventStore(BaseEventLog):
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        last_seen: BaselineStore,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._last_seen = last_seen
        self._append_lock = asyncio.Lock()

    @classmethod
    def connect(cls, dsn: str) -> EventStore:
        sessionmaker = build_async_sessionmaker(dsn)
        return cls(sessionmaker, BaselineStore(sessionmaker))

    async def append(self, events: Sequence[DomainEvent], baseline: LastSeenBatch) -> int:
        # single append funnel (lock) → gapless committed seq for readers
        async with self._append_lock, self._sessionmaker() as session, session.begin():
            for event in events:
                insert_stmt = pg_insert(EventLog).values(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    schema_version=event.schema_version,
                    aggregate_id=str(event.aggregate_id),
                    occurred_at=event.occurred_at,
                    content_hash=event.content_hash,
                    payload=encode_event(event),
                )
                await session.execute(
                    insert_stmt.on_conflict_do_nothing(index_elements=[EventLog.event_id])
                )
            await self._last_seen.apply(baseline, session=session)
            result = await session.execute(select(func.max(EventLog.seq)))
            return result.scalar_one_or_none() or 0

    async def read_after(self, seq: int, limit: int) -> list[DomainEvent]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(EventLog).where(EventLog.seq > seq).order_by(EventLog.seq).limit(limit)
            )
            return [_row_to_event(row) for row in result.scalars()]

    async def max_seq(self) -> int:
        async with self._sessionmaker() as session:
            result = await session.execute(select(func.max(EventLog.seq)))
            return result.scalar_one_or_none() or 0


def _row_to_event(row: EventLog) -> DomainEvent:
    return decode_event(
        event_id=row.event_id,
        event_type=row.event_type,
        aggregate_id=row.aggregate_id,
        occurred_at=row.occurred_at,
        content_hash=row.content_hash,
        schema_version=row.schema_version,
        seq=row.seq,
        payload=row.payload,
    )
