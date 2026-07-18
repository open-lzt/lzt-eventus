"""`PollingService` — filtered pending-event polling with explicit read tracking.

An alternative to webhook/stream delivery: register a `Subscription` with
`transport=polling` (via `SubscriptionAdminService.register`), then poll it
independently of every other subscription — each has its own `sink:<id>`
cursor, so N subscriptions can each track their own read position over the
same log. A caller can peek pending events without committing the cursor,
inspect them, then explicitly confirm via `confirm()` (either `read_all=true`
on the same request or a separate `/events/read_events` call). Unlike
`StreamService.catch_up`, which always advances the cursor past every scanned
event, `peek` never mutates state — an unconfirmed batch replays verbatim on
retry.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from lzt_eventus.codecs.json import event_envelope
from lzt_eventus.cursor.base import BaseCursorStore
from lzt_eventus.delivery.subscription import Subscription
from lzt_eventus.delivery.subscription_ctx import PollingCtx
from lzt_eventus.delivery.subscription_scope import to_filters
from lzt_eventus.errors import CursorConflict
from lzt_eventus.events.base import DomainEvent, EventType
from lzt_eventus.log.base import BaseEventLog
from lzt_eventus.web.base.errors import Conflict


@dataclass(frozen=True, slots=True)
class PendingEvent:
    seq: int
    event_type: EventType
    data: dict[str, object]


@dataclass(frozen=True, slots=True)
class PendingBatch:
    items: list[PendingEvent]
    next_seq: int
    last_read_seq: int
    drained: bool


class PollingService:
    def __init__(
        self,
        event_log: BaseEventLog,
        cursor_store: BaseCursorStore,
        *,
        max_confirm_retries: int = 1,
    ) -> None:
        self._log = event_log
        self._cursors = cursor_store
        self._max_confirm_retries = max_confirm_retries

    @staticmethod
    def _matches(
        sub: Subscription, event: DomainEvent, event_types: frozenset[EventType] | None
    ) -> bool:
        types = event_types if event_types is not None else sub.event_types
        if event.event_type not in types:
            return False
        return all(str(event.payload.get(key)) == val for key, val in to_filters(sub.scope).items())

    async def peek(
        self, sub: Subscription, event_types: frozenset[EventType] | None, limit: int
    ) -> PendingBatch:
        state = await self._cursors.get(sub.consumer_name())
        events = await self._log.read_after(state.last_seq, limit)
        items = [
            PendingEvent(seq=e.seq, event_type=e.event_type, data=event_envelope(e))
            for e in events
            if self._matches(sub, e, event_types)
        ]
        if not items and isinstance(sub.ctx, PollingCtx) and sub.ctx.poll_delay_seconds:
            # Long-poll emulation: hold an empty response so a quiet subscription's
            # client doesn't have to hammer this endpoint on its own tight loop.
            await asyncio.sleep(sub.ctx.poll_delay_seconds)
        next_seq = events[-1].seq if events else state.last_seq
        return PendingBatch(
            items=items,
            next_seq=next_seq,
            last_read_seq=state.last_seq,
            drained=len(events) < limit,
        )

    async def confirm(self, sub: Subscription, up_to_seq: int) -> int:
        """Advance `sub`'s cursor to `up_to_seq`; no-op if already past it."""
        name = sub.consumer_name()
        for attempt in range(self._max_confirm_retries + 1):
            state = await self._cursors.get(name)
            if up_to_seq <= state.last_seq:
                return state.last_seq
            try:
                await self._cursors.commit(name, up_to_seq, state.version)
            except CursorConflict as exc:
                if attempt == self._max_confirm_retries:
                    raise Conflict(
                        subscription_id=sub.subscription_id, up_to_seq=up_to_seq
                    ) from exc
                continue
            else:
                return up_to_seq
        raise AssertionError("unreachable")
