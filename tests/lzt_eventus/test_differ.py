"""`SnapshotDiffer` — every lot event carries `category` in `payload`, so a
subscription's `filters={"category": "steam"}` actually matches it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pylzt.models.lot import Lot
from pylzt.types import Category, Currency, ItemId, ItemOrigin, SellerId

from lzt_eventus.consumers.consumer import BaseSubscription
from lzt_eventus.diff.differ import SnapshotDiffer
from lzt_eventus.diff.snapshot import BaselineEntry, Snapshot
from lzt_eventus.events.base import EventType

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _lot(
    item_id: int, price: str, *, category: Category = Category.STEAM, content_hash: str = ""
) -> Lot:
    return Lot(
        item_id=ItemId(item_id),
        category=category,
        price=Decimal(price),
        currency=Currency.RUB,
        title="acc",
        seller_id=SellerId(1),
        published_at=_NOW,
        item_state="active",
        item_origin=ItemOrigin.BRUTE,
        guarantee="",
        nsb=True,
        content_hash=content_hash or price,
        attributes={},
    )


def test_new_lot_payload_carries_category() -> None:
    snapshot = Snapshot.from_lots(Category.FORTNITE, [_lot(1, "100", category=Category.FORTNITE)])
    result = SnapshotDiffer().diff({}, snapshot, poll_epoch=1, occurred_at=_NOW)

    assert [e.event_type for e in result.events] == [EventType.NEW_LOT]
    assert result.events[0].payload == {"category": "fortnite"}


def test_price_dropped_payload_carries_category() -> None:
    prev = {ItemId(1): BaselineEntry(price=Decimal("100"), content_hash="100", miss_count=0)}
    snapshot = Snapshot.from_lots(Category.STEAM, [_lot(1, "80", content_hash="80")])
    result = SnapshotDiffer().diff(prev, snapshot, poll_epoch=1, occurred_at=_NOW)

    assert [e.event_type for e in result.events] == [EventType.PRICE_DROPPED]
    assert result.events[0].payload == {"category": "steam"}


def test_lot_updated_payload_carries_category() -> None:
    prev = {ItemId(1): BaselineEntry(price=Decimal("100"), content_hash="v1", miss_count=0)}
    snapshot = Snapshot.from_lots(Category.STEAM, [_lot(1, "100", content_hash="v2")])
    result = SnapshotDiffer().diff(prev, snapshot, poll_epoch=1, occurred_at=_NOW)

    assert [e.event_type for e in result.events] == [EventType.LOT_UPDATED]
    assert result.events[0].payload == {"category": "steam"}


def test_subscription_category_filter_matches_and_rejects() -> None:
    """The whole point: a subscriber can now ask for one category among several polled."""
    steam_snapshot = Snapshot.from_lots(Category.STEAM, [_lot(1, "100", category=Category.STEAM)])
    fortnite_snapshot = Snapshot.from_lots(
        Category.FORTNITE, [_lot(2, "100", category=Category.FORTNITE)]
    )
    differ = SnapshotDiffer()
    steam_event = differ.diff({}, steam_snapshot, poll_epoch=1, occurred_at=_NOW).events[0]
    fortnite_event = differ.diff({}, fortnite_snapshot, poll_epoch=1, occurred_at=_NOW).events[0]

    sub = BaseSubscription(
        event_types=frozenset({EventType.NEW_LOT}), filters={"category": "steam"}
    )
    assert sub.matches(steam_event)
    assert not sub.matches(fortnite_event)
