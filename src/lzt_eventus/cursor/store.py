"""`CursorStore` — per-consumer cursor with optimistic version-guarded commit.

`commit` advances `last_seq` only if the stored `version` matches the caller's
`expected_version` (`UPDATE ... WHERE version = $expected`, or a guarded INSERT for
a brand-new consumer). Zero rows affected → another writer moved first → raise
`CursorConflict`, so two instances of the same consumer cannot silently clobber
each other's progress.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lzt_eventus.cursor.base import BaseCursorStore, CursorState
from lzt_eventus.errors import CursorConflict
from lzt_eventus.orm.base import build_async_sessionmaker
from lzt_eventus.orm.cursor import ConsumerCursor


class CursorStore(BaseCursorStore):
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    @classmethod
    def connect(cls, dsn: str) -> CursorStore:
        return cls(build_async_sessionmaker(dsn))

    async def get(self, consumer: str) -> CursorState:
        async with self._sessionmaker() as session:
            row = await session.get(ConsumerCursor, consumer)
            if row is None:
                return CursorState(last_seq=0, version=0)
            return CursorState(last_seq=row.last_seq, version=row.version)

    async def commit(self, consumer: str, seq: int, expected_version: int) -> None:
        now = datetime.now(UTC)
        async with self._sessionmaker() as session, session.begin():
            if expected_version == 0:
                insert_stmt = pg_insert(ConsumerCursor).values(
                    consumer_name=consumer, last_seq=seq, version=1, updated_at=now
                )
                result = await session.execute(
                    insert_stmt.on_conflict_do_nothing(
                        index_elements=[ConsumerCursor.consumer_name]
                    )
                )
                if cast("CursorResult[object]", result).rowcount == 0:
                    raise CursorConflict(
                        consumer, expected_version, await self._version(session, consumer)
                    )
                return
            stmt = (
                update(ConsumerCursor)
                .where(
                    ConsumerCursor.consumer_name == consumer,
                    ConsumerCursor.version == expected_version,
                )
                .values(last_seq=seq, version=ConsumerCursor.version + 1, updated_at=now)
            )
            result = await session.execute(stmt)
            if cast("CursorResult[object]", result).rowcount == 0:
                raise CursorConflict(
                    consumer, expected_version, await self._version(session, consumer)
                )

    async def delete(self, consumer: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(ConsumerCursor, consumer)
            if row is not None:
                await session.delete(row)

    async def watermark(self) -> int | None:
        async with self._sessionmaker() as session:
            result = await session.execute(select(func.min(ConsumerCursor.last_seq)))
            return result.scalar_one_or_none()

    @staticmethod
    async def _version(session: AsyncSession, consumer: str) -> int:
        row = await session.get(ConsumerCursor, consumer)
        return row.version if row is not None else 0
