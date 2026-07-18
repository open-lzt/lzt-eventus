"""Dead-letter store — parks poison events so one bad event can't HOL-block.

When a consumer's `handle()` fails past `max_handle_attempts`, the bus parks the
event here, advances the cursor, and emits a gauge. `redrive.sh` re-injects parked
events after a fix (the inverse of park).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lzt_eventus.codecs.json import decode_event, encode_event
from lzt_eventus.events.base import DomainEvent
from lzt_eventus.orm.base import build_async_sessionmaker
from lzt_eventus.orm.dead_letter import DeadLetter as DeadLetterRow


@dataclass(frozen=True, slots=True)
class DeadLetter:
    consumer: str
    seq: int
    event: DomainEvent
    reason: str


class BaseDeadLetterStore(ABC):
    @abstractmethod
    async def park(self, consumer: str, event: DomainEvent, reason: str) -> None: ...

    @abstractmethod
    async def list_for(self, consumer: str) -> list[DeadLetter]: ...

    @abstractmethod
    async def drain(self, consumer: str) -> list[DeadLetter]:
        """Remove and return a consumer's parked events (for redrive)."""

    @abstractmethod
    async def count(self) -> int: ...


class DeadLetterStore(BaseDeadLetterStore):
    """Durable DLQ — parked events survive a daemon restart.

    `decode_event` needs the event's base fields (type, aggregate_id, occurred_at,
    content_hash, schema_version) which the `dead_letter` columns don't carry, so
    `park` persists a small `_meta` envelope alongside `encode_event`'s payload in
    the JSONB; `drain`/`list_for` rebuild a base `DomainEvent` from it.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    @classmethod
    def connect(cls, dsn: str) -> DeadLetterStore:
        return cls(build_async_sessionmaker(dsn))

    async def park(self, consumer: str, event: DomainEvent, reason: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            session.add(
                DeadLetterRow(
                    consumer_name=consumer,
                    seq=event.seq,
                    event_id=event.event_id,
                    reason=reason,
                    payload=_pack(event),
                )
            )

    async def list_for(self, consumer: str) -> list[DeadLetter]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(DeadLetterRow)
                .where(DeadLetterRow.consumer_name == consumer)
                .order_by(DeadLetterRow.id)
            )
            return [_row_to_dead_letter(row) for row in result.scalars()]

    async def drain(self, consumer: str) -> list[DeadLetter]:
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                select(DeadLetterRow)
                .where(DeadLetterRow.consumer_name == consumer)
                .order_by(DeadLetterRow.id)
            )
            drained = [_row_to_dead_letter(row) for row in result.scalars()]
            await session.execute(
                delete(DeadLetterRow).where(DeadLetterRow.consumer_name == consumer)
            )
            return drained

    async def count(self) -> int:
        async with self._sessionmaker() as session:
            result = await session.execute(select(func.count()).select_from(DeadLetterRow))
            return result.scalar_one()


class MemoryDeadLetterStore(BaseDeadLetterStore):
    """In-memory DLQ — embedded runtime backing for `EventEngine.build_memory()`."""

    def __init__(self) -> None:
        self._parked: list[DeadLetter] = []

    async def park(self, consumer: str, event: DomainEvent, reason: str) -> None:
        self._parked.append(DeadLetter(consumer, event.seq, event, reason))

    async def list_for(self, consumer: str) -> list[DeadLetter]:
        return [d for d in self._parked if d.consumer == consumer]

    async def drain(self, consumer: str) -> list[DeadLetter]:
        drained = [d for d in self._parked if d.consumer == consumer]
        self._parked = [d for d in self._parked if d.consumer != consumer]
        return drained

    async def count(self) -> int:
        return len(self._parked)


def _pack(event: DomainEvent) -> dict[str, object]:
    return {
        "_meta": {
            "event_type": event.event_type.value,
            "aggregate_id": str(event.aggregate_id),
            "occurred_at": event.occurred_at.isoformat(),
            "content_hash": event.content_hash,
            "schema_version": event.schema_version,
        },
        "payload": encode_event(event),
    }


def _row_to_dead_letter(row: DeadLetterRow) -> DeadLetter:
    stored: Mapping[str, object] = row.payload
    meta = cast("Mapping[str, object]", stored["_meta"])
    payload = cast("Mapping[str, object]", stored["payload"])
    event = decode_event(
        event_id=row.event_id,
        event_type=str(meta["event_type"]),
        aggregate_id=str(meta["aggregate_id"]),
        occurred_at=datetime.fromisoformat(str(meta["occurred_at"])),
        content_hash=str(meta["content_hash"]),
        schema_version=int(cast("int", meta["schema_version"])),
        seq=row.seq,
        payload=payload,
    )
    return DeadLetter(consumer=row.consumer_name, seq=row.seq, event=event, reason=row.reason)
