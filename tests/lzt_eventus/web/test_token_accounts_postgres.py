"""Regression test for a real bug found live: `PostgresTokenAccountRepo.add()`
raised `AliasAlreadyExists` (masking a real `ForeignKeyViolationError`) on the
FIRST-EVER call against a fresh, empty database — 100% reproducible, not a race.

Root cause: `TokenAccountRow` and `AliasRow` have no ORM `relationship()`
between them (by design — this repo's rows never use `relationship()`), so
SQLAlchemy's unit-of-work has no dependency edge telling it to insert the
account row before the alias row within one flush, and could (and did, on
real Postgres) emit the alias INSERT first, hitting the FK constraint.
`MemoryTokenAccountRepo` can't catch this class of bug — it doesn't enforce
FKs — so this needs a real Postgres. Per this repo's own convention (see
test_migration_token_account.py's docstring), CI has no live Postgres; this
test is skipped unless `LZT_TEST_DATABASE_URL` points at a real, disposable
database (verified live against a throwaway prod database before/after the
fix — see the account/reconciler mini-plan history for the incident).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from lzt_eventus.account.token_account import TokenAccount, TokenAccountId
from lzt_eventus.orm.base import BaseOrm, build_async_sessionmaker
from lzt_eventus.web.repos.token_account_repo import PostgresTokenAccountRepo

_DSN = os.environ.get("LZT_TEST_DATABASE_URL", "")

requires_real_postgres = pytest.mark.skipif(
    not _DSN, reason="set LZT_TEST_DATABASE_URL to a real, disposable Postgres DSN to run this"
)


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine]:
    eng = create_async_engine(_DSN)
    async with eng.begin() as conn:
        await conn.run_sync(BaseOrm.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(BaseOrm.metadata.drop_all)
    await eng.dispose()


@requires_real_postgres
async def test_add_does_not_hit_fk_violation_on_fresh_db(engine: AsyncEngine) -> None:
    # str(url) masks the password (renders "***") — need the real DSN here.
    sessionmaker = build_async_sessionmaker(engine.url.render_as_string(hide_password=False))
    repo = PostgresTokenAccountRepo(sessionmaker)
    account = TokenAccount(
        account_id=TokenAccountId(uuid4().hex),
        token_ciphertext=b"fake-ciphertext",
        created_at=datetime.now(UTC),
    )

    result = await repo.add(account, "env-0")

    assert result.account_id == account.account_id
    fetched = await repo.get_by_alias("env-0")
    assert fetched is not None
    assert fetched.account_id == account.account_id


@requires_real_postgres
async def test_add_is_reliable_across_repeated_fresh_accounts(engine: AsyncEngine) -> None:
    """The original bug was non-deterministic-looking (worked in Memory tests,
    failed on real Postgres) — run several inserts to guard against a fix that
    only happens to pass once."""
    # str(url) masks the password (renders "***") — need the real DSN here.
    sessionmaker = build_async_sessionmaker(engine.url.render_as_string(hide_password=False))
    repo = PostgresTokenAccountRepo(sessionmaker)

    for i in range(5):
        account = TokenAccount(
            account_id=TokenAccountId(uuid4().hex),
            token_ciphertext=b"fake-ciphertext",
            created_at=datetime.now(UTC),
        )
        await repo.add(account, f"env-{i}")

    assert await repo.count() == 5
