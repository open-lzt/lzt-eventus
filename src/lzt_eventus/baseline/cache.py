"""`RedisLastSeenCache` — a hot mirror of the durable baseline, recoverable from PG.

This is a **cache, never the source of truth**. The differ reads it first; on a
miss it falls back to `BaselineStore`. A Redis flush/eviction therefore
costs only an extra Postgres read — it must NOT produce a false `NewLotAppeared`
flood, because the durable baseline still holds the lots. Entries are stored per
category as a Redis hash keyed by `item_id`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal

from pylzt.types import Category, ItemId
from redis.asyncio import Redis

from lzt_eventus.diff.snapshot import BaselineEntry

_KEY_PREFIX = "lastseen"


def _key(category: Category) -> str:
    return f"{_KEY_PREFIX}:{category.value}"


def _encode(entry: BaselineEntry) -> str:
    return json.dumps(
        {
            "price": str(entry.price),
            "content_hash": entry.content_hash,
            "miss_count": entry.miss_count,
        }
    )


def _decode(raw: str | bytes) -> BaselineEntry:
    data = json.loads(raw)
    return BaselineEntry(
        price=Decimal(str(data["price"])),
        content_hash=str(data["content_hash"]),
        miss_count=int(data["miss_count"]),
    )


class RedisLastSeenCache:
    def __init__(self, client: Redis) -> None:
        self._redis = client

    @classmethod
    def connect(cls, url: str) -> RedisLastSeenCache:
        return cls(Redis.from_url(url))

    async def warm(self, category: Category, entries: Mapping[ItemId, BaselineEntry]) -> None:
        """Replace the cached hash for a category from the durable baseline."""
        key = _key(category)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            if entries:
                pipe.hset(key, mapping={str(int(i)): _encode(e) for i, e in entries.items()})
            await pipe.execute()

    async def get_entry(self, category: Category, item_id: ItemId) -> BaselineEntry | None:
        raw = await self._redis.hget(_key(category), str(int(item_id)))
        if raw is None:
            return None
        return _decode(raw)

    async def put(self, category: Category, item_id: ItemId, entry: BaselineEntry) -> None:
        await self._redis.hset(_key(category), str(int(item_id)), _encode(entry))

    async def drop(self, category: Category, item_id: ItemId) -> None:
        await self._redis.hdel(_key(category), str(int(item_id)))

    async def clear(self, category: Category) -> None:
        await self._redis.delete(_key(category))
