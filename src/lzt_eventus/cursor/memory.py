"""In-memory `BaseCursorStore` — embedded runtime backing for `EventEngine.build_memory()`."""

from __future__ import annotations

import asyncio

from lzt_eventus.cursor.base import BaseCursorStore, CursorState
from lzt_eventus.errors import CursorConflict


class MemoryCursorStore(BaseCursorStore):
    def __init__(self) -> None:
        self._cursors: dict[str, CursorState] = {}
        self._lock = asyncio.Lock()

    async def get(self, consumer: str) -> CursorState:
        return self._cursors.get(consumer, CursorState(last_seq=0, version=0))

    async def commit(self, consumer: str, seq: int, expected_version: int) -> None:
        async with self._lock:
            current = self._cursors.get(consumer, CursorState(last_seq=0, version=0))
            if current.version != expected_version:
                raise CursorConflict(consumer, expected_version, current.version)
            self._cursors[consumer] = CursorState(last_seq=seq, version=expected_version + 1)

    async def delete(self, consumer: str) -> None:
        async with self._lock:
            self._cursors.pop(consumer, None)

    async def watermark(self) -> int | None:
        if not self._cursors:
            return None
        return min(c.last_seq for c in self._cursors.values())
