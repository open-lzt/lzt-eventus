"""Payment-operation events (3-E) — one shared field set, twelve pinned subtypes.

`PaymentOperationEvent` carries every field `PaymentsSource` reads off a raw
`/user/payments` operation; each concrete subclass only pins `EVENT_TYPE` to the
matching `EventType` member so the dispatch table in the source stays a
`dict[str, type[PaymentOperationEvent]]` lookup, not an if/elif chain.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

from pylzt.models.payment import PaymentOperation

from lzt_eventus.events.base import AggregateId, DomainEvent, EventType, make_event_id


class PaymentOperationEvent(DomainEvent):
    operation_id: int
    amount: Decimal
    currency: str
    counterparty_id: int | None
    counterparty_username: str
    fee: int
    is_hold: bool
    hold_end: datetime | None
    comment: str

    @classmethod
    def from_operation(
        cls,
        op: PaymentOperation,
        *,
        occurred_at: datetime,
        content_hash: str,
        poll_epoch: int,
    ) -> PaymentOperationEvent | None:
        """Dispatch a raw operation to its typed event, or None if the type is unmapped.

        Unmapped `operation_type`s are an open upstream set (LZT adds new ones), so the
        caller skips + marks-seen rather than raising — see `PaymentsSource.poll_once`.
        Construction is explicit + typed (no `**dict` spread), so a field typo is a
        static error, not a silently-dropped kwarg.
        """
        event_cls = _BY_OPERATION_TYPE.get(op.operation_type)
        if event_cls is None:
            return None
        aggregate_id = AggregateId(str(op.operation_id))
        return event_cls(
            event_id=make_event_id(aggregate_id, event_cls.EVENT_TYPE, content_hash, poll_epoch),
            aggregate_id=aggregate_id,
            occurred_at=occurred_at,
            content_hash=content_hash,
            operation_id=op.operation_id,
            amount=op.amount,
            currency=op.currency,
            counterparty_id=op.counterparty_id,
            counterparty_username=op.counterparty_username,
            fee=op.fee,
            is_hold=op.is_hold,
            hold_end=_hold_end(op),
            comment=op.comment,
        )


class IncomeReceived(PaymentOperationEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.INCOME_RECEIVED


class ExpenseRecorded(PaymentOperationEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.EXPENSE_RECORDED


class BalanceRefilled(PaymentOperationEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.BALANCE_REFILLED


class BalanceWithdrawn(PaymentOperationEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.BALANCE_WITHDRAWN


class ItemPurchased(PaymentOperationEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.ITEM_PURCHASED


class ItemSold(PaymentOperationEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.ITEM_SOLD


class MoneyTransferred(PaymentOperationEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.MONEY_TRANSFERRED


class MoneyReceived(PaymentOperationEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.MONEY_RECEIVED


class InternalPurchase(PaymentOperationEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.INTERNAL_PURCHASE


class HoldClaimed(PaymentOperationEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.HOLD_CLAIMED


class AutoPaymentTriggered(PaymentOperationEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.AUTO_PAYMENT_TRIGGERED


class BalanceExchanged(PaymentOperationEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.BALANCE_EXCHANGED


def _hold_end(op: PaymentOperation) -> datetime | None:
    return datetime.fromtimestamp(op.hold_end_date, tz=UTC) if op.hold_end_date else None


# `operation_type` -> event subclass. Not every live value is guaranteed to be one of
# these (research-derived, unverified against a live token) — `from_operation` returns
# None for unmapped values, which `PaymentsSource` logs and skips, never raises.
_BY_OPERATION_TYPE: dict[str, type[PaymentOperationEvent]] = {
    "income": IncomeReceived,
    "expense": ExpenseRecorded,
    "refill": BalanceRefilled,
    "withdraw": BalanceWithdrawn,
    "paid_item": ItemPurchased,
    "sold_item": ItemSold,
    "transfer_to": MoneyTransferred,
    "transfer_from": MoneyReceived,
    "internal_purchase": InternalPurchase,
    "hold_claimed": HoldClaimed,
    "auto_payment": AutoPaymentTriggered,
    "exchange": BalanceExchanged,
}
