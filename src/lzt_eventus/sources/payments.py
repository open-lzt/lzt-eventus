"""`PaymentsSource` — turns `/user/payments` operations into typed domain events.

One instance per account (`token_id` names its cursor consumer, `payments:{token_id}`,
per `~/.claude/rules/patterns.md` cursor-key convention). `ListPayments` only supports
"older than X" pagination (`operation_id_lt`), so each tick re-reads from the newest
page and pages *backward* only far enough to close the gap down to the persisted
high-water mark (`BaseCursorStore`, `last_seq` repurposed as "highest operation_id
processed" rather than log seq — same per-consumer string-key store, different unit,
same "optimistic commit" semantics). Cold start (`version == 0`) seeds the watermark
from one page only — no historical flood, mirroring `CategorySource._bootstrap`.

`BaseSeenCache` is the real dedup guard (operation_id + content_hash of
operation_id/type/amount — catches a mutated record even under an unconfirmed
id-monotonicity guarantee); the cursor is purely a pagination-stop optimization.
`BaseEventLog.append` requires a `LastSeenBatch` even though this source has no
catalog baseline of its own — it shares `Category.OTHER`'s epoch counter with
`GuaranteeWatcher` (Discover-before-write: no new storage ABC), always via
read-current-then-+1 so the shared counter only ever advances, never regresses.
"""

from __future__ import annotations

import hashlib
from typing import Final

import structlog
from pylzt.client import Client
from pylzt.methods.payments import ListPayments
from pylzt.models.payment import PaymentOperation
from pylzt.types import Category, ItemId

from lzt_eventus.baseline.store import BaseLastSeenStore, LastSeenBatch
from lzt_eventus.cursor.base import BaseCursorStore
from lzt_eventus.dedup.seen import BaseSeenCache
from lzt_eventus.events.base import DomainEvent
from lzt_eventus.events.payment import (
    PaymentOperationEvent,
)
from lzt_eventus.lib.clock import Clock, RealClock
from lzt_eventus.sources.base import BaseSource
from lzt_eventus.transport import BaseTransport

_log = structlog.get_logger("lzt_eventus.sources.payments")

# Shared with `GuaranteeWatcher` — both only ever read-then-+1 this bucket's epoch,
# so cohabiting is safe (monotonic regardless of interleaving) despite neither
# source having a real catalog baseline to store here.
_BUCKET: Final[Category] = Category.OTHER

# Bounds one poll_once's backward-pagination cost when a large gap has accrued
# (e.g. after downtime) — the tick stops here and closes the rest next cadence.
_MAX_PAGES_PER_TICK: Final[int] = 20


def _content_hash(op: PaymentOperation) -> str:
    """Guards against a mutated record surviving under the same operation_id."""
    raw = f"{op.operation_id}:{op.operation_type}:{op.amount}"
    return hashlib.sha256(raw.encode()).hexdigest()


class PaymentsSource(BaseSource):
    def __init__(
        self,
        *,
        client: Client,
        token_id: str,
        transport: BaseTransport,
        seen: BaseSeenCache,
        cursor: BaseCursorStore,
        last_seen: BaseLastSeenStore,
        min_cadence: float,
        max_cadence: float,
        cadence: float,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(
            min_cadence=min_cadence,
            max_cadence=max_cadence,
            cadence=cadence,
            clock=clock or RealClock(),
        )
        self.name = f"source:payments:{token_id}"
        self._consumer = f"payments:{token_id}"
        self._client = client
        self._transport = transport
        self._seen = seen
        self._cursor = cursor
        self._last_seen = last_seen
        self._clock = clock or RealClock()

    async def _collect_new_operations(
        self, low_watermark: int, cold_start: bool
    ) -> list[PaymentOperation]:
        """Page backward from the newest operation until the watermark gap closes."""
        new_ops: list[PaymentOperation] = []
        operation_id_lt: int | None = None
        for _ in range(_MAX_PAGES_PER_TICK):
            page = await self._client.execute(ListPayments(operation_id_lt=operation_id_lt))
            if not page.items:
                break
            reached_watermark = False
            for op in page.items:
                if op.operation_id <= low_watermark:
                    reached_watermark = True
                    break
                new_ops.append(op)
            if cold_start:
                break  # bootstrap: seed the watermark from the newest page only
            if reached_watermark or not page.has_more:
                break
            operation_id_lt = page.items[-1].operation_id
        return new_ops

    async def poll_once(self) -> int:
        cursor_state = await self._cursor.get(self._consumer)
        low_watermark = cursor_state.last_seq
        cold_start = cursor_state.version == 0

        new_ops = await self._collect_new_operations(low_watermark, cold_start)
        highest_seen = max([low_watermark, *(op.operation_id for op in new_ops)])

        events: list[DomainEvent] = []
        now = self._clock.now()
        epoch = await self._last_seen.get_poll_epoch(_BUCKET) + 1
        for op in reversed(new_ops):  # oldest-first: stable occurred_at/epoch ordering
            content_hash = _content_hash(op)
            if await self._seen.is_seen(ItemId(op.operation_id), content_hash):
                continue
            event = PaymentOperationEvent.from_operation(
                op, occurred_at=now, content_hash=content_hash, poll_epoch=epoch
            )
            if event is None:
                _log.warning(
                    "payments_unmapped_operation_type",
                    operation_type=op.operation_type,
                    operation_id=op.operation_id,
                )
                await self._seen.mark(ItemId(op.operation_id), content_hash)
                continue
            events.append(event)
            await self._seen.mark(ItemId(op.operation_id), content_hash)

        if events:
            batch = LastSeenBatch(category=_BUCKET, poll_epoch=epoch)
            await self._transport.send(events, batch)

        if highest_seen > low_watermark:
            await self._cursor.commit(self._consumer, highest_seen, cursor_state.version)

        return len(events)
