"""Declarative base + async session factory shared by every ORM model.

`BaseOrm.metadata` is the single source of truth Alembic targets
(`target_metadata` in `env.py`). The engine uses the `postgresql+asyncpg` driver
— asyncpg is only the wire driver under SQLAlchemy's async layer, never used
directly.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class BaseOrm(DeclarativeBase):
    """Root declarative class; all tables register on `BaseOrm.metadata`."""


def build_async_sessionmaker(dsn: str) -> async_sessionmaker[AsyncSession]:
    """Build an `async_sessionmaker` over a fresh async engine for `dsn`.

    `expire_on_commit=False` keeps attributes readable after a commit (the stores
    return plain values, never lazy-load post-commit). A bare `postgresql://`
    DSN (the documented `.env.example` / `LZT_DATABASE_URL` default, and what a
    non-SQLAlchemy tool like `psql` expects) picks psycopg2 by default, which
    isn't installed — normalize to the asyncpg driver here, mirroring the same
    rewrite `alembic/env.py` already does for migrations.
    """
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(dsn, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)
