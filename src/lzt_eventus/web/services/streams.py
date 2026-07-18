"""`StreamService` — turn the durable log into a per-subscription event feed.

Reads the in-process `event_log` via `read_after`, keeps only events that match a
subscription (`event_type` in the catalog AND `filters` ⊆ payload), and serializes
each into a JSON-safe wire dict (`encode_event` + the base envelope fields).

`catch_up` drains the backlog one bounded batch at a time; `live` polls on a short
interval (the log is in-process). The cursor a caller carries between `catch_up`
and `live` is the last *scanned* seq — never the last *matching* seq — so a resume
skips no events even when the matching ones are sparse (zero-gap).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from lzt_eventus.codecs.json import event_envelope
from lzt_eventus.delivery.subscription import Subscription
from lzt_eventus.delivery.subscription_scope import to_filters
from lzt_eventus.events.base import DomainEvent, EventType
from lzt_eventus.log.base import BaseEventLog

_DEFAULT_MAX_BATCH = 500
_DEFAULT_POLL_INTERVAL = 0.25


@dataclass(frozen=True, slots=True)
class StreamFrame:
    """One serialized event ready for an SSE/WS frame."""

    seq: int
    event_type: EventType
    data: dict[str, object]


@dataclass(frozen=True, slots=True)
class CatchUpBatch:
    """One bounded catch-up read: matching frames + the last scanned seq."""

    frames: list[StreamFrame]
    next_seq: int
    drained: bool


class StreamService:
    def __init__(
        self,
        event_log: BaseEventLog,
        *,
        max_batch: int = _DEFAULT_MAX_BATCH,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._log = event_log
        self._max_batch = max_batch
        self._poll_interval = poll_interval

    @staticmethod
    def _matches(sub: Subscription, event: DomainEvent) -> bool:
        if event.event_type not in sub.event_types:
            return False
        return all(str(event.payload.get(key)) == val for key, val in to_filters(sub.scope).items())

    @staticmethod
    def _frame(event: DomainEvent) -> StreamFrame:
        return StreamFrame(seq=event.seq, event_type=event.event_type, data=event_envelope(event))

    async def catch_up(self, sub: Subscription, after_seq: int) -> CatchUpBatch:
        events = await self._log.read_after(after_seq, self._max_batch)
        frames = [self._frame(e) for e in events if self._matches(sub, e)]
        next_seq = events[-1].seq if events else after_seq
        return CatchUpBatch(frames=frames, next_seq=next_seq, drained=len(events) < self._max_batch)

    async def live(
        self, sub: Subscription, after_seq: int, stop: asyncio.Event
    ) -> AsyncIterator[StreamFrame]:
        cursor = after_seq
        while not stop.is_set():
            events = await self._log.read_after(cursor, self._max_batch)
            for event in events:
                cursor = event.seq
                if self._matches(sub, event):
                    yield self._frame(event)
            if len(events) < self._max_batch:
                await asyncio.sleep(self._poll_interval)
