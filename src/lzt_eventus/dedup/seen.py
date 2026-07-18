"""Seen-set pre-filter — a cache that skips diffing unchanged lots.

`seen:{item_id}:{content_hash}` marks a lot+hash already processed. This is a
**pre-filter only**: a miss merely costs one extra diff, never a lost or
duplicated event (a real price change has a new hash → not pre-filtered → still
emitted). The durable `event_log` UNIQUE on `event_id` is the real idempotency
guard; this set just trims work.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pylzt.types import ItemId
from redis.asyncio import Redis


def _key(item_id: ItemId, content_hash: str) -> str:
    return f"seen:{int(item_id)}:{content_hash}"


class BaseSeenCache(ABC):
    @abstractmethod
    async def is_seen(self, item_id: ItemId, content_hash: str) -> bool: ...

    @abstractmethod
    async def mark(self, item_id: ItemId, content_hash: str) -> None: ...


class SeenCache(BaseSeenCache):
    def __init__(self, client: Redis, ttl_seconds: int) -> None:
        self._redis = client
        self._ttl = ttl_seconds

    @classmethod
    def connect(cls, url: str, ttl_seconds: int) -> SeenCache:
        return cls(Redis.from_url(url), ttl_seconds)

    async def is_seen(self, item_id: ItemId, content_hash: str) -> bool:
        return bool(await self._redis.exists(_key(item_id, content_hash)))

    async def mark(self, item_id: ItemId, content_hash: str) -> None:
        await self._redis.set(_key(item_id, content_hash), "1", ex=self._ttl)


class MemorySeenCache(BaseSeenCache):
    """In-memory seen-set — embedded runtime backing for `EventEngine.build_memory()`."""

    def __init__(self) -> None:
        self._seen: set[tuple[ItemId, str]] = set()

    async def is_seen(self, item_id: ItemId, content_hash: str) -> bool:
        return (item_id, content_hash) in self._seen

    async def mark(self, item_id: ItemId, content_hash: str) -> None:
        self._seen.add((item_id, content_hash))
