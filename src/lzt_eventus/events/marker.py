"""Cold-start marker — one event instead of a per-lot `NewLotAppeared` flood.

On a category's first ever poll the baseline is empty; a naive diff would emit
`NewLotAppeared` for the whole live catalog. Bootstrap mode seeds the baseline
silently and records exactly one `SnapshotInitialized` (§A2).
"""

from __future__ import annotations

from typing import ClassVar

from pylzt.types import Category

from lzt_eventus.events.base import DomainEvent, EventType


class SnapshotInitialized(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.SNAPSHOT_INITIALIZED
    category: Category
    lot_count: int
