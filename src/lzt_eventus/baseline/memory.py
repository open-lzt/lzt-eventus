"""In-memory `BaseLastSeenStore` — embedded runtime backing for `EventEngine.build_memory()`."""

from __future__ import annotations

from pylzt.types import Category, ItemId

from lzt_eventus.baseline.store import BaseLastSeenStore, LastSeenBatch
from lzt_eventus.diff.snapshot import BaselineEntry


class MemoryLastSeenStore(BaseLastSeenStore):
    def __init__(self) -> None:
        self._data: dict[Category, dict[ItemId, BaselineEntry]] = {}
        self._epoch: dict[Category, int] = {}
        self._bootstrapped: set[Category] = set()

    async def has_baseline(self, category: Category) -> bool:
        return category in self._bootstrapped

    async def get_baseline(self, category: Category) -> dict[ItemId, BaselineEntry]:
        return dict(self._data.get(category, {}))

    async def get_poll_epoch(self, category: Category) -> int:
        return self._epoch.get(category, 0)

    async def apply(self, batch: LastSeenBatch) -> None:
        bucket = self._data.setdefault(batch.category, {})
        for item_id in batch.drops:
            bucket.pop(item_id, None)
        bucket.update(batch.upserts)
        self._epoch[batch.category] = batch.poll_epoch
        self._bootstrapped.add(batch.category)
