"""Pending-events polling DTOs — the `read_all` / `read_events` wire contract."""

from __future__ import annotations

from lzt_eventus.events.base import EventType
from lzt_eventus.web.base.schema import BaseSchema
from lzt_eventus.web.services.polling import PendingBatch


class PendingEventOut(BaseSchema):
    seq: int
    event_type: EventType
    data: dict[str, object]


class PendingEventsOut(BaseSchema):
    subscription_id: str
    items: list[PendingEventOut]
    next_seq: int
    last_read_seq: int
    drained: bool
    committed: bool

    @classmethod
    def of(cls, subscription_id: str, batch: PendingBatch, *, committed: bool) -> PendingEventsOut:
        return cls(
            subscription_id=subscription_id,
            items=[
                PendingEventOut(seq=i.seq, event_type=i.event_type, data=i.data)
                for i in batch.items
            ],
            next_seq=batch.next_seq,
            last_read_seq=batch.last_read_seq,
            drained=batch.drained,
            committed=committed,
        )


class ReadEventsRequest(BaseSchema):
    subscription_id: str
    up_to_seq: int


class ReadEventsOut(BaseSchema):
    subscription_id: str
    last_seq: int
