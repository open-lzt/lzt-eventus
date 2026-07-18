"""`LoggingConsumer` — the open-closed proof: a real subscriber, zero engine edits.

It subscribes to the catalog events and logs them. The first *real* consumer is
the future `lzt-pulse` collector (separate repo) — it plugs in the same way.
"""

from __future__ import annotations

import structlog

from lzt_eventus.consumers.consumer import BaseConsumer, BaseSubscription
from lzt_eventus.events.base import DomainEvent, EventType

_log = structlog.get_logger("lzt_eventus.logging_module")

_CATALOG_EVENTS = frozenset(
    {
        EventType.NEW_LOT,
        EventType.PRICE_DROPPED,
        EventType.LOT_UPDATED,
        EventType.LOT_DISAPPEARED,
        EventType.SNAPSHOT_INITIALIZED,
    }
)


class LoggingConsumer(BaseConsumer):
    name = "logging"

    def __init__(self, event_types: frozenset[EventType] = _CATALOG_EVENTS) -> None:
        self.subscriptions = [BaseSubscription[DomainEvent](event_types=event_types)]
        self.seen: list[DomainEvent] = []

    async def handle(self, event: DomainEvent) -> None:
        self.seen.append(event)
        _log.info(
            "event",
            type=event.event_type.value,
            seq=event.seq,
            aggregate=event.aggregate_id,
        )
