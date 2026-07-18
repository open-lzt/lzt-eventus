"""Content-hash determinism regression test — pinned pre-pydantic-migration digests.

The values below were captured against the original `@dataclass` `DomainEvent`
implementation (one per event family + one with a nested `Lot` value) and must
stay byte-identical after the pydantic migration, or replay-idempotency breaks
(the same logical event would re-hash to a different `event_id`).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal

from pylzt.models.lot import Lot
from pylzt.types import Category, Currency, ItemId, ItemOrigin, SellerId

from lzt_eventus.codecs.json import canonical_bytes, event_envelope
from lzt_eventus.events.account import DisputeOpened
from lzt_eventus.events.base import AggregateId
from lzt_eventus.events.lot import LotReserved, NewLotAppeared, PurchaseConfirmed
from lzt_eventus.events.reputation import RatingChanged

_OCCURRED = datetime(2026, 1, 1, tzinfo=UTC)

_PINNED = {
    "dispute": (
        "39a19f25-209e-5a1a-91f8-96c9080d8c7e",
        "1c99144e442ec0d12a94881675b309974ed3484d47367f0aafd98e6bffd14e9d",
    ),
    "lot_reserved": (
        "e5768aca-bd6d-5697-898f-a1467ebecba6",
        "7a82860c51ffeb6dd9a1a034576a69541445164328aaec1d1a71ed96db8f3929",
    ),
    "purchase_confirmed": (
        "ce723708-4c11-5e30-a3be-ebd0d9d7939c",
        "c9b484e86cc6da784c0d454f484cbad5498e79865e7ff7c5decf5224ba6bcd91",
    ),
    "rating_changed": (
        "f6e6846a-062b-599c-967b-adbb997982a8",
        "f4616246f3124489d881afdb57a1cf0ee42dcdaf7ad2415bdb7de75d61901863",
    ),
    "new_lot_appeared": (
        "0668afcd-d637-582a-8833-33cb16ad4abe",
        "1f7b93dc80a395dbee07f223a62d0b9ceceb97f73c36507ee9edd58beaa8c9f5",
    ),
}


def _digest(event: object) -> str:
    return hashlib.sha256(canonical_bytes(event_envelope(event))).hexdigest()  # type: ignore[arg-type]


def test_dispute_opened_hash_unchanged() -> None:
    event = DisputeOpened.build(
        aggregate_id=AggregateId("item-1"),
        occurred_at=_OCCURRED,
        content_hash="hash-1",
        poll_epoch=1,
        claim_id=555,
        item_id=999,
    )
    event_id, digest = _PINNED["dispute"]
    assert str(event.event_id) == event_id
    assert _digest(event) == digest


def test_lot_reserved_hash_unchanged() -> None:
    event = LotReserved.build(
        aggregate_id=AggregateId("item-2"),
        occurred_at=_OCCURRED,
        content_hash="hash-2",
        poll_epoch=1,
        item_id=111,
        buyer_id=222,
    )
    event_id, digest = _PINNED["lot_reserved"]
    assert str(event.event_id) == event_id
    assert _digest(event) == digest


def test_purchase_confirmed_hash_unchanged() -> None:
    event = PurchaseConfirmed.build(
        aggregate_id=AggregateId("item-3"),
        occurred_at=_OCCURRED,
        content_hash="hash-3",
        poll_epoch=1,
        item_id=333,
    )
    event_id, digest = _PINNED["purchase_confirmed"]
    assert str(event.event_id) == event_id
    assert _digest(event) == digest


def test_rating_changed_hash_unchanged() -> None:
    event = RatingChanged.build(
        aggregate_id=AggregateId("self"),
        occurred_at=_OCCURRED,
        content_hash="hash-5",
        poll_epoch=1,
        user_like_count=10,
        user_dislike_count=2,
        delta_likes=1,
        delta_dislikes=0,
    )
    event_id, digest = _PINNED["rating_changed"]
    assert str(event.event_id) == event_id
    assert _digest(event) == digest


def test_new_lot_appeared_hash_unchanged_with_nested_lot_value() -> None:
    """The one sample with a nested (still-dataclass) `Lot` field value — proves
    `to_jsonable`'s nested-dataclass branch still fires correctly post-migration."""
    lot = Lot(
        item_id=ItemId(42),
        category=Category.STEAM,
        price=Decimal("99.50"),
        currency=Currency.RUB,
        title="acc",
        seller_id=SellerId(1),
        published_at=_OCCURRED,
        item_state="active",
        item_origin=ItemOrigin.BRUTE,
        guarantee="",
        nsb=True,
        content_hash="99.50:acc:active",
        attributes={},
    )
    event = NewLotAppeared.build(
        aggregate_id=AggregateId("item-42"),
        occurred_at=_OCCURRED,
        content_hash="hash-lot",
        poll_epoch=1,
        lot=lot,
    )
    event_id, digest = _PINNED["new_lot_appeared"]
    assert str(event.event_id) == event_id
    assert _digest(event) == digest
