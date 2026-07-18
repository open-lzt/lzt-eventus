"""Alembic environment — async (asyncpg), DSN from `LZT_DATABASE_URL`.

`target_metadata = BaseOrm.metadata` so the schema autogenerate-diffs against the
ORM models. The hand-written `0001` migration is authoritative for the partitioned
`event_log` (RANGE-by-month DDL Alembic cannot express via autogenerate).
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from lzt_eventus.orm import BaseOrm

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = BaseOrm.metadata

_DEFAULT_DSN = "postgresql+asyncpg://lzt:lzt@localhost:5432/aiolzt"


def _dsn() -> str:
    raw = os.environ.get("LZT_DATABASE_URL", _DEFAULT_DSN)
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


def run_migrations_offline() -> None:
    context.configure(
        url=_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _dsn()
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
