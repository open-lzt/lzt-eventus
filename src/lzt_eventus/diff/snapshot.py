"""Value objects for the diff: a poll `Snapshot` and a durable `BaselineEntry`.

`BaselineEntry` is the minimal per-lot state the differ needs to decide an event
(price + content_hash + miss_count); it lives durably in `last_seen` so a daemon
restart never drops a pending disappearance. `Snapshot` is one poll's view.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from pylzt.models.lot import Lot
from pylzt.types import Category, ItemId


@dataclass(frozen=True, slots=True)
class BaselineEntry:
    price: Decimal
    content_hash: str
    miss_count: int = 0


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One poll's current view of a category, keyed by item id."""

    category: Category
    lots: Mapping[ItemId, Lot] = field(default_factory=dict)

    @classmethod
    def from_lots(cls, category: Category, lots: Sequence[Lot]) -> Snapshot:
        return cls(category=category, lots={lot.item_id: lot for lot in lots})

    def ids(self) -> frozenset[ItemId]:
        return frozenset(self.lots)
