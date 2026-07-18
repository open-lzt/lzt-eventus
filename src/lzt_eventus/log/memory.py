"""In-memory `BaseEventLog` — embedded runtime backing for `EventEngine.build_memory()`."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from uuid import UUID

from lzt_eventus.baseline.store import BaseLastSeenStore, LastSeenBatch
from lzt_eventus.events.base import DomainEvent
from lzt_eventus.log.base import BaseEventLog


class MemoryEventLog(BaseEventLog):
    def __init__(self, last_seen: BaseLastSeenStore) -> None:
        self._last_seen = last_seen
        self._events: list[DomainEvent] = []
        self._seen_ids: set[UUID] = set()
        self._lock = asyncio.Lock()

    async def append(self, events: Sequence[DomainEvent], baseline: LastSeenBatch) -> int:
        async with self._lock:  # single funnel → gapless committed seq
            for event in events:
                if event.event_id in self._seen_ids:
                    continue
                seq = len(self._events) + 1
                self._events.append(event.with_seq(seq))
                self._seen_ids.add(event.event_id)
            await self._last_seen.apply(baseline)
            return len(self._events)

    async def read_after(self, seq: int, limit: int) -> list[DomainEvent]:
        return [e for e in self._events if e.seq > seq][:limit]

    async def max_seq(self) -> int:
        return len(self._events)
