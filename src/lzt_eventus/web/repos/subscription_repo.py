"""Subscription repository — in-memory (devkit) + Postgres impl (ABC lives in delivery/).

The in-memory backend exists so the whole management API is exercisable with no
database (the zero-infra devkit path); its CRUD/ordering is inherited from the shared
`InMemoryRepo`.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lzt_eventus.delivery.repo import BaseSubscriptionRepo
from lzt_eventus.delivery.subscription import Subscription, SubscriptionId, TransportKind
from lzt_eventus.delivery.subscription_ctx import SubscriptionCtx
from lzt_eventus.delivery.subscription_scope import SubscriptionScope
from lzt_eventus.events.base import EventType
from lzt_eventus.orm.base import build_async_sessionmaker
from lzt_eventus.web.base.repo import InMemoryRepo
from lzt_eventus.web.orm.subscription import SubscriptionRow

_CTX_ADAPTER: TypeAdapter[SubscriptionCtx] = TypeAdapter(SubscriptionCtx)
_SCOPE_ADAPTER: TypeAdapter[SubscriptionScope] = TypeAdapter(SubscriptionScope)


class MemorySubscriptionRepo(InMemoryRepo[Subscription, SubscriptionId], BaseSubscriptionRepo):
    async def get(self, subscription_id: SubscriptionId) -> Subscription | None:
        return self._items.get(subscription_id)

    async def add(self, sub: Subscription) -> Subscription:
        self._items[sub.subscription_id] = sub
        return sub

    async def replace(self, sub: Subscription) -> Subscription:
        self._items[sub.subscription_id] = sub
        return sub


def _to_row(sub: Subscription) -> SubscriptionRow:
    return SubscriptionRow(
        subscription_id=str(sub.subscription_id),
        transport=sub.transport.value,
        endpoint=sub.endpoint,
        event_types=sorted(et.value for et in sub.event_types),
        scope=sub.scope.model_dump(mode="json"),
        ctx=sub.ctx.model_dump(mode="json"),
        secret=sub.secret,
        stream_token_hash=sub.stream_token_hash,
        active=sub.active,
        created_at=sub.created_at,
    )


def _to_domain(row: SubscriptionRow) -> Subscription:
    return Subscription(
        subscription_id=SubscriptionId(row.subscription_id),
        transport=TransportKind(row.transport),
        endpoint=row.endpoint,
        event_types=frozenset(EventType(et) for et in row.event_types),
        created_at=row.created_at,
        ctx=_CTX_ADAPTER.validate_python(row.ctx),
        scope=_SCOPE_ADAPTER.validate_python(row.scope),
        secret=row.secret,
        stream_token_hash=row.stream_token_hash,
        active=row.active,
    )


class PostgresSubscriptionRepo(BaseSubscriptionRepo):
    """Durable subscription store over SQLAlchemy async ORM."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    @classmethod
    def connect(cls, dsn: str) -> PostgresSubscriptionRepo:
        return cls(build_async_sessionmaker(dsn))

    async def get(self, subscription_id: SubscriptionId) -> Subscription | None:
        async with self._sessionmaker() as session:
            row = await session.get(SubscriptionRow, str(subscription_id))
            return _to_domain(row) if row is not None else None

    async def list(
        self, *, limit: int, offset: int, active_only: bool = False
    ) -> Sequence[Subscription]:
        stmt = select(SubscriptionRow).order_by(SubscriptionRow.created_at)
        if active_only:
            stmt = stmt.where(SubscriptionRow.active.is_(True))
        stmt = stmt.limit(limit).offset(offset)
        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            return [_to_domain(row) for row in result.scalars()]

    async def count(self, *, active_only: bool = False) -> int:
        stmt = select(func.count()).select_from(SubscriptionRow)
        if active_only:
            stmt = stmt.where(SubscriptionRow.active.is_(True))
        async with self._sessionmaker() as session:
            result = await session.execute(stmt)
            return result.scalar_one()

    async def add(self, sub: Subscription) -> Subscription:
        async with self._sessionmaker() as session, session.begin():
            session.add(_to_row(sub))
        return sub

    async def replace(self, sub: Subscription) -> Subscription:
        async with self._sessionmaker() as session, session.begin():
            await session.merge(_to_row(sub))
        return sub
