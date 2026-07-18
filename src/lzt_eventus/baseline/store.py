"""Durable diff baseline (`last_seen`) — the source of truth, Redis only mirrors it.

The baseline holds, per (category, item_id): last price, content_hash, and the
durable `miss_count` (so a restart never drops a pending `LotDisappeared`). The
per-category `poll_epoch` lives here too and is bumped **in the same transaction**
as the events it seeds (§A3) — a crash between bump and persist would change the
epoch on replay and break deterministic-id dedup.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pylzt.types import Category, ItemId
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lzt_eventus.diff.snapshot import BaselineEntry
from lzt_eventus.orm.base import build_async_sessionmaker
from lzt_eventus.orm.last_seen import LastSeen, PollEpoch


@dataclass(frozen=True, slots=True)
class LastSeenBatch:
    """The baseline mutation applied atomically alongside an event append."""

    category: Category
    poll_epoch: int
    upserts: Mapping[ItemId, BaselineEntry] = field(default_factory=dict)
    drops: frozenset[ItemId] = field(default_factory=frozenset)


class BaseLastSeenStore(ABC):
    @abstractmethod
    async def has_baseline(self, category: Category) -> bool:
        """True once a category has been bootstrapped (decides bootstrap vs diff)."""

    @abstractmethod
    async def get_baseline(self, category: Category) -> dict[ItemId, BaselineEntry]:
        """Full durable baseline for a category."""

    @abstractmethod
    async def get_poll_epoch(self, category: Category) -> int:
        """Current durable poll-cycle counter (0 before first bootstrap)."""

    @abstractmethod
    async def apply(self, batch: LastSeenBatch) -> None:
        """Apply upserts + drops + persist `poll_epoch`. Called inside the log txn."""


class BaselineStore(BaseLastSeenStore):
    """Durable baseline over SQLAlchemy async ORM — the source of truth.

    `apply` joins the log's transaction: when `EventStore.append` passes its
    `session`, the upserts + poll_epoch bump commit atomically alongside the
    events; otherwise `apply` opens its own transaction.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    @classmethod
    def connect(cls, dsn: str) -> BaselineStore:
        return cls(build_async_sessionmaker(dsn))

    async def has_baseline(self, category: Category) -> bool:
        async with self._sessionmaker() as session:
            row = await session.get(PollEpoch, category.value)
            return row is not None

    async def get_baseline(self, category: Category) -> dict[ItemId, BaselineEntry]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(LastSeen).where(LastSeen.category == category.value)
            )
            return {
                ItemId(row.item_id): BaselineEntry(
                    price=row.price,
                    content_hash=row.content_hash,
                    miss_count=row.miss_count,
                )
                for row in result.scalars()
            }

    async def get_poll_epoch(self, category: Category) -> int:
        async with self._sessionmaker() as session:
            row = await session.get(PollEpoch, category.value)
            return row.epoch if row is not None else 0

    async def apply(self, batch: LastSeenBatch, session: AsyncSession | None = None) -> None:
        if session is not None:
            await self._apply(batch, session)
            return
        async with self._sessionmaker() as own_session, own_session.begin():
            await self._apply(batch, own_session)

    async def _apply(self, batch: LastSeenBatch, session: AsyncSession) -> None:
        now = datetime.now(UTC)
        for item_id, entry in batch.upserts.items():
            insert_stmt = pg_insert(LastSeen).values(
                category=batch.category.value,
                item_id=int(item_id),
                price=entry.price,
                content_hash=entry.content_hash,
                miss_count=entry.miss_count,
                last_polled_at=now,
            )
            upsert = insert_stmt.on_conflict_do_update(
                index_elements=[LastSeen.category, LastSeen.item_id],
                set_={
                    "price": insert_stmt.excluded.price,
                    "content_hash": insert_stmt.excluded.content_hash,
                    "miss_count": insert_stmt.excluded.miss_count,
                    "last_polled_at": insert_stmt.excluded.last_polled_at,
                },
            )
            await session.execute(upsert)
        for dropped in batch.drops:
            await session.execute(
                delete(LastSeen).where(
                    LastSeen.category == batch.category.value,
                    LastSeen.item_id == int(dropped),
                )
            )
        epoch_insert = pg_insert(PollEpoch).values(
            category=batch.category.value, epoch=batch.poll_epoch
        )
        await session.execute(
            epoch_insert.on_conflict_do_update(
                index_elements=[PollEpoch.category],
                set_={"epoch": epoch_insert.excluded.epoch},
            )
        )
