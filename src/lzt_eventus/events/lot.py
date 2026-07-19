"""Catalog domain events (3-A) — the only family emitted in wave-02.

Each is a frozen subclass of `DomainEvent` so a subscriber can filter by type and
type-check against the concrete payload. `content_hash` (carried on the base) is
the stable hash over meaningful fields only — volatile churn never fires events.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import ClassVar

from pydantic import Field
from pylzt.models.lot import Lot

from lzt_eventus.events.base import DomainEvent, EventType


class DisappearReason(StrEnum):
    SOLD = "sold"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    NORMAL = "normal"
    LOW = "low"


class NewLotAppeared(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.NEW_LOT
    lot: Lot


class PriceDropped(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.PRICE_DROPPED
    old_price: Decimal
    new_price: Decimal
    lot: Lot


class LotUpdated(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.LOT_UPDATED
    lot: Lot
    changed: frozenset[str] = Field(default_factory=frozenset)


class LotDisappeared(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.LOT_DISAPPEARED
    reason: DisappearReason = DisappearReason.UNKNOWN
    confidence: Confidence = Confidence.LOW


class LotReserved(DomainEvent):
    """A buyer put the lot on hold — notification-sourced, not diff-sourced."""

    EVENT_TYPE: ClassVar[EventType] = EventType.LOT_RESERVED
    item_id: int
    buyer_id: int | None = None


class PurchaseConfirmed(DomainEvent):
    """The seller confirmed a purchase — distinct from the `ITEM_SOLD` payment event."""

    EVENT_TYPE: ClassVar[EventType] = EventType.PURCHASE_CONFIRMED
    item_id: int
    buyer_id: int | None = None
