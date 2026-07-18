"""`BaseEventLog` — the durable append-only log, single source of truth.

`append` is **atomic**: it writes the events AND applies the baseline batch in one
transaction (no cross-store txn with Redis). Appends are **serialized** through one
funnel so the committed `seq` is gapless for readers — a consumer at `cursor=N`
never skips an in-flight lower `seq`. The deterministic `event_id` is UNIQUE, so a
crash-replay re-append is a no-op (idempotent at the source of truth).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from lzt_eventus.baseline.store import LastSeenBatch
from lzt_eventus.events.base import DomainEvent


class BaseEventLog(ABC):
    @abstractmethod
    async def append(self, events: Sequence[DomainEvent], baseline: LastSeenBatch) -> int:
        """Atomically persist events + apply the baseline; return the last seq."""

    @abstractmethod
    async def read_after(self, seq: int, limit: int) -> list[DomainEvent]:
        """Events with `seq > given`, in ascending gapless seq order."""

    @abstractmethod
    async def max_seq(self) -> int:
        """Highest committed seq (0 if empty) — for lag gauges and retention."""
