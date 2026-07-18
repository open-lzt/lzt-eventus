from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pylzt.lib.clock import FakeClock
from pylzt.methods.payments import ListPayments
from pylzt.models.payment import PaymentOperation
from pylzt.pagination import Page

from eventus_fakes import FakeCursorStore, FakeEventLog, FakeLastSeenStore, FakeSeenCache
from lzt_eventus.events.payment import (
    IncomeReceived,
    ItemPurchased,
    ItemSold,
    PaymentOperationEvent,
)
from lzt_eventus.sources.payments import PaymentsSource
from lzt_eventus.transport import LogTransport

_TOKEN_ID = "tok1"


def _op(operation_id: int, operation_type: str) -> PaymentOperation:
    return PaymentOperation(
        operation_id=operation_id,
        operation_type=operation_type,
        amount=Decimal("10.00"),
        currency="RUB",
        counterparty_id=555,
        counterparty_username="someone",
        fee=0,
        is_hold=False,
        hold_end_date=None,
        item_id=0,
        comment="",
    )


# Newest-first, as `/user/payments` returns it. 102 is deliberately an operation_type
# the mapping table doesn't cover (must be skipped, not crash).
_FIXTURE_PAGE = [
    _op(104, "sold_item"),
    _op(103, "paid_item"),
    _op(102, "some_future_type"),
    _op(101, "income"),
]


class FakeClient:
    """Stub `Client.execute` — single-page fixture, ignores `operation_id_lt`."""

    def __init__(self, ops: list[PaymentOperation]) -> None:
        self._ops = ops

    async def execute(self, method: ListPayments) -> Page[PaymentOperation]:
        return Page(items=self._ops, has_more=False)


class FakeBus:
    def __init__(self) -> None:
        self.notified = 0

    def notify(self) -> None:
        self.notified += 1


def _source(
    client: FakeClient, log: FakeEventLog, last_seen: FakeLastSeenStore, bus: FakeBus
) -> PaymentsSource:
    return PaymentsSource(
        client=client,  # type: ignore[arg-type]
        token_id=_TOKEN_ID,
        transport=LogTransport(log, on_committed=bus.notify),
        seen=FakeSeenCache(),
        cursor=FakeCursorStore(),
        last_seen=last_seen,
        min_cadence=1.0,
        max_cadence=60.0,
        cadence=5.0,
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
    )


async def test_emits_mapped_events_and_skips_unmapped_operation_type() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()
    client = FakeClient(list(_FIXTURE_PAGE))
    source = _source(client, log, last_seen, bus)

    emitted = await source.poll_once()

    assert emitted == 3  # 104, 103, 101 — 102's type has no mapping
    assert bus.notified == 1
    payment_events = [e for e in log._events if isinstance(e, PaymentOperationEvent)]
    by_id = {e.operation_id: e for e in payment_events}
    assert isinstance(by_id[104], ItemSold)
    assert isinstance(by_id[103], ItemPurchased)
    assert isinstance(by_id[101], IncomeReceived)
    assert 102 not in by_id


async def test_second_poll_with_same_fixture_emits_nothing_new() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()
    client = FakeClient(list(_FIXTURE_PAGE))
    source = _source(client, log, last_seen, bus)

    first = await source.poll_once()
    second = await source.poll_once()

    assert first == 3
    assert second == 0
    assert len(log._events) == 3  # no duplicate append on replay
