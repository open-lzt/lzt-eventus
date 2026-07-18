"""Event egress seam — where a source sends the events it produced.

A source doesn't know how or where its events are stored: it produces typed
events and calls `transport.send(events, baseline)`. `LogTransport` is the default
(outbox pattern) — it appends to the durable `EventStore` (events + baseline in one
atomic txn) and wakes the catch-up bus. A future `WebhookTransport`/`QueueTransport`
swaps in without touching any source. This is the write side; the read side stays
the bus (pull-by-cursor).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence

from lzt_eventus.baseline.store import LastSeenBatch
from lzt_eventus.events.base import DomainEvent
from lzt_eventus.log.base import BaseEventLog


class BaseTransport(ABC):
    @abstractmethod
    async def send(self, events: Sequence[DomainEvent], baseline: LastSeenBatch) -> int:
        """Persist events + baseline atomically; return the committed max seq."""


class LogTransport(BaseTransport):
    """Default outbox transport: append to the `EventStore`, then wake the bus.

    `on_committed` (the bus's `notify`) is fired only when events were actually
    committed — a baseline-only write (e.g. a poll-epoch bump with no events) wakes
    nothing. Ordering matches the old inline path: append commits, then notify.
    """

    def __init__(
        self, store: BaseEventLog, *, on_committed: Callable[[], None] | None = None
    ) -> None:
        self._store = store
        self._on_committed = on_committed

    async def send(self, events: Sequence[DomainEvent], baseline: LastSeenBatch) -> int:
        seq = await self._store.append(events, baseline)
        if events and self._on_committed is not None:
            self._on_committed()
        return seq
