"""`NotificationsSource` — dispatch by content_type + dedup-on-replay."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pylzt.models.notification import Notification
from pylzt.pagination import Page

from eventus_fakes import FakeCursorStore, FakeEventLog, FakeLastSeenStore, FakeSeenCache
from lzt_eventus.events.account import ClaimFiled, DisputeOpened
from lzt_eventus.events.base import DomainEvent
from lzt_eventus.events.lot import LotReserved, PurchaseConfirmed
from lzt_eventus.events.notification import (
    ForumNotificationReceived,
    MarketNotificationReceived,
    NotificationExtraInvalid,
    parse_notification,
)
from lzt_eventus.sources.notifications import NotificationsSource
from lzt_eventus.transport import LogTransport

_CREATED_AT = 1_735_689_600


@dataclass(slots=True)
class _Fixture:
    notification_id: int
    content_type: str
    extra: dict[str, object]


def _notification(fixture: _Fixture) -> Notification:
    return Notification(
        notification_id=fixture.notification_id,
        content_type=fixture.content_type,
        created_at=_CREATED_AT,
        extra=fixture.extra,
    )


# market feed: one recognized (dispute) + one unmatched (falls back)
_MARKET_FIXTURES = [
    _Fixture(2, "market_dispute_opened", {"claim_id": 555, "item_id": 999}),
    _Fixture(1, "market_new_review", {}),
]
# nomarket feed: one unmatched content_type (falls back)
_NOMARKET_FIXTURES = [_Fixture(10, "forum_warn_issued", {})]


class FakeClient:
    """Stub `Client.execute` — routes `ListNotifications` to fixture pages by kind+limit."""

    def __init__(self) -> None:
        self._full = {
            "market": [_notification(f) for f in _MARKET_FIXTURES],
            "nomarket": [_notification(f) for f in _NOMARKET_FIXTURES],
        }

    async def execute(self, method: object) -> Page[Notification]:
        kind = method.type  # type: ignore[attr-defined]
        limit = method.limit  # type: ignore[attr-defined]
        items = self._full[kind]
        if limit == 1:
            return Page(items=items[:1], has_more=bool(items))
        return Page(items=items, has_more=False)


class FakeBus:
    def __init__(self) -> None:
        self.notified = 0

    def notify(self) -> None:
        self.notified += 1


def _source(client: FakeClient) -> tuple[NotificationsSource, FakeEventLog, FakeBus]:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()
    source = NotificationsSource(
        client=client,  # type: ignore[arg-type]
        transport=LogTransport(log, on_committed=bus.notify),
        last_seen=last_seen,
        cursors=FakeCursorStore(),
        seen=FakeSeenCache(),
        min_cadence=1.0,
        max_cadence=60.0,
        cadence=15.0,
    )
    return source, log, bus


async def test_dispatches_matched_and_fallback_events() -> None:
    client = FakeClient()
    source, log, bus = _source(client)

    emitted = await source.poll_once()

    assert emitted == 3
    events: list[DomainEvent] = log._events
    disputes = [e for e in events if isinstance(e, DisputeOpened)]
    market_fallback = [e for e in events if isinstance(e, MarketNotificationReceived)]
    forum_fallback = [e for e in events if isinstance(e, ForumNotificationReceived)]

    assert len(disputes) == 1
    assert disputes[0].claim_id == 555
    assert disputes[0].item_id == 999

    assert len(market_fallback) == 1
    assert market_fallback[0].notification_id == 1
    assert market_fallback[0].content_type == "market_new_review"

    assert len(forum_fallback) == 1
    assert forum_fallback[0].notification_id == 10

    assert not any(isinstance(e, ClaimFiled) for e in events)
    assert bus.notified == 1


_RESERVED_FIXTURES = [
    _Fixture(3, "market_lot_reserved", {"item_id": 111, "buyer_id": 222}),
    _Fixture(4, "market_purchase_confirmed", {"item_id": 333}),
]


class _ReservedFakeClient(FakeClient):
    def __init__(self) -> None:
        self._full = {
            "market": [_notification(f) for f in _RESERVED_FIXTURES],
            "nomarket": [],
        }


async def test_dispatches_lot_reserved_and_purchase_confirmed() -> None:
    client = _ReservedFakeClient()
    source, log, _bus = _source(client)

    emitted = await source.poll_once()

    assert emitted == 2
    events: list[DomainEvent] = log._events
    reserved = [e for e in events if isinstance(e, LotReserved)]
    confirmed = [e for e in events if isinstance(e, PurchaseConfirmed)]

    assert len(reserved) == 1
    assert reserved[0].item_id == 111
    assert reserved[0].buyer_id == 222

    assert len(confirmed) == 1
    assert confirmed[0].item_id == 333
    assert confirmed[0].buyer_id is None


def test_present_non_numeric_extra_field_raises_typed_error() -> None:
    fixture = _Fixture(2, "market_dispute_opened", {"claim_id": "not-a-number", "item_id": 999})
    notification = _notification(fixture)

    with pytest.raises(NotificationExtraInvalid) as exc_info:
        parse_notification(
            notification,
            kind="market",
            fallback_cls=MarketNotificationReceived,
            content_hash="hash",
            poll_epoch=1,
        )

    assert exc_info.value.notification_id == 2


async def test_replay_is_deduped() -> None:
    client = FakeClient()
    source, log, _bus = _source(client)

    first = await source.poll_once()
    second = await source.poll_once()

    assert first == 3
    assert second == 0
    assert len(log._events) == 3  # no re-emission on an unchanged fixture page
