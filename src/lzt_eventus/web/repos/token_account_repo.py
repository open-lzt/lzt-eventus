"""TokenAccount repository — in-memory (devkit) + Postgres impl of `account.repo`'s ABC.

The in-memory backend exists so the whole management API is exercisable with no
database (the zero-infra devkit path); its account CRUD/ordering is inherited from
the shared `InMemoryRepo`, and it keeps its own alias index on top.
`AliasAlreadyExists` is raised the same way from both backends: in-memory checks the
dict up front, Postgres relies on the `token_alias.alias` PRIMARY KEY and translates
the resulting `IntegrityError`.
"""

from __future__ import annotations

from collections.abc import Sequence

from pylzt.types import Category
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lzt_eventus.account.repo import BaseTokenAccountRepo
from lzt_eventus.account.token_account import Alias, TokenAccount, TokenAccountId
from lzt_eventus.orm.base import build_async_sessionmaker
from lzt_eventus.web.base.errors import AliasAlreadyExists
from lzt_eventus.web.base.repo import InMemoryRepo
from lzt_eventus.web.orm.token_account import AliasRow, TokenAccountRow


class MemoryTokenAccountRepo(InMemoryRepo[TokenAccount, TokenAccountId], BaseTokenAccountRepo):
    def __init__(self) -> None:
        super().__init__()
        self._aliases: dict[str, Alias] = {}

    async def get(self, account_id: TokenAccountId) -> TokenAccount | None:
        return self._items.get(account_id)

    async def get_by_alias(self, alias: str) -> TokenAccount | None:
        row = self._aliases.get(alias)
        if row is None:
            return None
        return self._items.get(row.account_id)

    async def alias_exists(self, alias: str) -> bool:
        return alias in self._aliases

    async def add(self, account: TokenAccount, primary_alias: str) -> TokenAccount:
        if primary_alias in self._aliases:
            raise AliasAlreadyExists(alias=primary_alias)
        self._items[account.account_id] = account
        self._aliases[primary_alias] = Alias(
            alias=primary_alias,
            account_id=account.account_id,
            created_at=account.created_at,
            is_primary=True,
        )
        return account

    async def add_alias(self, account_id: TokenAccountId, alias: str) -> Alias:
        if alias in self._aliases:
            raise AliasAlreadyExists(alias=alias)
        account = self._items[account_id]
        row = Alias(alias=alias, account_id=account_id, created_at=account.created_at)
        self._aliases[alias] = row
        return row

    async def list_aliases(self, account_id: TokenAccountId) -> Sequence[Alias]:
        return [a for a in self._aliases.values() if a.account_id == account_id]

    async def replace(self, account: TokenAccount) -> TokenAccount:
        self._items[account.account_id] = account
        return account

    async def list_active(self) -> Sequence[TokenAccount]:
        return self._filtered(active_only=True)


def _to_row(account: TokenAccount) -> TokenAccountRow:
    return TokenAccountRow(
        account_id=str(account.account_id),
        token_ciphertext=account.token_ciphertext,
        metadata_=dict(account.metadata),
        categories=[c.value for c in account.categories],
        active=account.active,
        created_at=account.created_at,
    )


def _to_domain(row: TokenAccountRow) -> TokenAccount:
    return TokenAccount(
        account_id=TokenAccountId(row.account_id),
        token_ciphertext=row.token_ciphertext,
        created_at=row.created_at,
        metadata=dict(row.metadata_),
        categories=tuple(Category.parse(c) for c in row.categories),
        active=row.active,
    )


def _alias_to_domain(row: AliasRow) -> Alias:
    return Alias(
        alias=row.alias,
        account_id=TokenAccountId(row.account_id),
        created_at=row.created_at,
        is_primary=row.is_primary,
    )


class PostgresTokenAccountRepo(BaseTokenAccountRepo):
    """Durable token-account store over SQLAlchemy async ORM."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    @classmethod
    def connect(cls, dsn: str) -> PostgresTokenAccountRepo:
        return cls(build_async_sessionmaker(dsn))

    async def get(self, account_id: TokenAccountId) -> TokenAccount | None:
        async with self._sessionmaker() as session:
            row = await session.get(TokenAccountRow, str(account_id))
            return _to_domain(row) if row is not None else None

    async def get_by_alias(self, alias: str) -> TokenAccount | None:
        async with self._sessionmaker() as session:
            alias_row = await session.get(AliasRow, alias)
            if alias_row is None:
                return None
            row = await session.get(TokenAccountRow, alias_row.account_id)
            return _to_domain(row) if row is not None else None

    async def alias_exists(self, alias: str) -> bool:
        async with self._sessionmaker() as session:
            return await session.get(AliasRow, alias) is not None

    async def add(self, account: TokenAccount, primary_alias: str) -> TokenAccount:
        try:
            async with self._sessionmaker() as session, session.begin():
                session.add(_to_row(account))
                # Explicit flush before the alias insert: TokenAccountRow and AliasRow
                # have no ORM `relationship()` between them (by design, matching the
                # rest of this repo's relationship-free style), so SQLAlchemy's
                # unit-of-work has no dependency edge telling it to insert the account
                # row before the alias row in the same flush — without this, it can
                # (and on a real Postgres, reproducibly did) emit the alias INSERT
                # first and hit the FK constraint. Still one transaction: a later
                # failure rolls back both inserts together.
                await session.flush()
                session.add(
                    AliasRow(
                        alias=primary_alias,
                        account_id=str(account.account_id),
                        is_primary=True,
                        created_at=account.created_at,
                    )
                )
        except IntegrityError as exc:
            raise AliasAlreadyExists(alias=primary_alias) from exc
        return account

    async def add_alias(self, account_id: TokenAccountId, alias: str) -> Alias:
        try:
            async with self._sessionmaker() as session, session.begin():
                row = AliasRow(alias=alias, account_id=str(account_id), is_primary=False)
                session.add(row)
        except IntegrityError as exc:
            raise AliasAlreadyExists(alias=alias) from exc
        return _alias_to_domain(row)

    async def list_aliases(self, account_id: TokenAccountId) -> Sequence[Alias]:
        stmt = select(AliasRow).where(AliasRow.account_id == str(account_id))
        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            return [_alias_to_domain(row) for row in result.scalars()]

    async def replace(self, account: TokenAccount) -> TokenAccount:
        async with self._sessionmaker() as session, session.begin():
            await session.merge(_to_row(account))
        return account

    async def list(
        self, *, limit: int, offset: int, active_only: bool = False
    ) -> Sequence[TokenAccount]:
        stmt = select(TokenAccountRow).order_by(TokenAccountRow.created_at)
        if active_only:
            stmt = stmt.where(TokenAccountRow.active.is_(True))
        stmt = stmt.limit(limit).offset(offset)
        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            return [_to_domain(row) for row in result.scalars()]

    async def count(self, *, active_only: bool = False) -> int:
        stmt = select(func.count()).select_from(TokenAccountRow)
        if active_only:
            stmt = stmt.where(TokenAccountRow.active.is_(True))
        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            return result.scalar_one()

    async def list_active(self) -> Sequence[TokenAccount]:
        stmt = select(TokenAccountRow).where(TokenAccountRow.active.is_(True))
        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            return [_to_domain(row) for row in result.scalars()]
