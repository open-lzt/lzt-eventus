"""`RetentionWorker` — bound log growth without ever dropping unread events.

Pruning is gated on the consumer watermark (`min(last_seq)` across cursors), NOT
time alone: a row is deleted only when it is both older than `retention_months`
AND below the watermark, so a lagging consumer blocks pruning of its unread rows.
The actual delete is a Postgres concern behind the `BasePruner` seam (`NullPruner`
is a no-op). `event_log` is not partitioned, so retention is DELETE-based, not
DROP PARTITION (see `orm/event_log.py` — D20 > A4).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import cast

import structlog
from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lzt_eventus.cursor.base import BaseCursorStore
from lzt_eventus.orm.base import build_async_sessionmaker
from lzt_eventus.orm.event_log import EventLog

_log = structlog.get_logger("lzt_eventus.retention")

_DAYS_PER_MONTH = 30


class BasePruner(ABC):
    @abstractmethod
    async def prune_below(self, watermark_seq: int, retention_months: int) -> int:
        """Delete events older than `retention_months` AND at/below `watermark_seq`.

        Returns the number of rows pruned.
        """


class NullPruner(BasePruner):
    async def prune_below(self, watermark_seq: int, retention_months: int) -> int:
        return 0


class PgRetentionPruner(BasePruner):
    """DELETE-based retention over `event_log` (no partitions — D20 > A4).

    Deletes committed events strictly gated on the consumer watermark: never a row
    a live cursor still needs. Runs in bounded batches so a large backlog cannot
    take a long table lock. `retention_months` is approximated as 30-day months.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        batch_size: int = 10_000,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._batch_size = batch_size

    @classmethod
    def connect(cls, dsn: str) -> PgRetentionPruner:
        return cls(build_async_sessionmaker(dsn))

    async def prune_below(self, watermark_seq: int, retention_months: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=_DAYS_PER_MONTH * retention_months)
        total = 0
        async with self._sessionmaker() as session:
            while True:
                batch_ids = (
                    select(EventLog.seq)
                    .where(EventLog.seq <= watermark_seq, EventLog.occurred_at < cutoff)
                    .limit(self._batch_size)
                )
                async with session.begin():
                    result = await session.execute(
                        delete(EventLog).where(EventLog.seq.in_(batch_ids))
                    )
                deleted = cast("CursorResult[object]", result).rowcount
                total += deleted
                if deleted < self._batch_size:
                    return total


class RetentionWorker:
    def __init__(
        self,
        cursors: BaseCursorStore,
        pruner: BasePruner,
        *,
        retention_months: int,
    ) -> None:
        self._cursors = cursors
        self._pruner = pruner
        self._retention_months = retention_months

    async def run_once(self) -> int:
        watermark = await self._cursors.watermark()
        if watermark is None:
            return 0  # no consumers → nothing safe to prune
        pruned = await self._pruner.prune_below(watermark, self._retention_months)
        if pruned:
            _log.info("events_pruned", count=pruned, watermark=watermark)
        return pruned
