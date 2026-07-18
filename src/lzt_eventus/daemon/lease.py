"""Single-owner daemon lease — a second instance must refuse to run.

A Postgres advisory lock is the natural fit: it is connection-scoped and released
automatically if the owner dies. Prevents double-emit / cursor races between two
daemons pointed at the same database. The `NullLease` is the in-memory/test default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from lzt_eventus.errors import AlreadyRunning


class BaseLease(ABC):
    @abstractmethod
    async def acquire(self) -> None:
        """Take the lease, or raise `AlreadyRunning` if held elsewhere."""

    @abstractmethod
    async def release(self) -> None: ...


class NullLease(BaseLease):
    """No-op lease for single-process / in-memory runs."""

    async def acquire(self) -> None:
        return None

    async def release(self) -> None:
        return None


class PgAdvisoryLease(BaseLease):
    """`pg_try_advisory_lock(key)` on a dedicated connection held for the run.

    `engine` is a SQLAlchemy `AsyncEngine`, typed `Any` so the lib surface never
    imports SQLAlchemy; the daemon supplies the real engine.
    """

    def __init__(self, engine: Any, lock_key: int) -> None:
        self._engine = engine
        self._lock_key = lock_key
        self._conn: Any | None = None

    async def acquire(self) -> None:
        from sqlalchemy import text

        conn = await self._engine.connect()
        result = await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self._lock_key})
        if not result.scalar():
            await conn.close()
            raise AlreadyRunning(self._lock_key)
        self._conn = conn

    async def release(self) -> None:
        if self._conn is None:
            return
        from sqlalchemy import text

        await self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self._lock_key})
        await self._conn.close()
        self._conn = None
