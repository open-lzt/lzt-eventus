"""Domain-event base + the full `EventType` catalog.

`event_id` is a *deterministic* uuid5 of `(aggregate_id, event_type,
content_hash, poll_epoch)` — so a crash before the append commits, followed by a
re-poll that re-diffs the same snapshot, regenerates the *same* id and collides
on the `event_log` UNIQUE constraint instead of double-emitting. `poll_epoch` is
the durable per-category poll-cycle counter (NOT wall-clock) for exactly that
replay-stability. Evolution is additive-only (`extra="ignore"` on read); a
breaking change later introduces a `BaseEventUpcaster`, not a backfill.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, NewType, Self

from pydantic import BaseModel, ConfigDict, Field

AggregateId = NewType("AggregateId", str)

# Stable namespace for deterministic event ids — never change (would break dedup).
_EVENT_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00cf4fc964ff")


class EventType(StrEnum):
    NEW_LOT = "new_lot"
    PRICE_DROPPED = "price_dropped"
    LOT_UPDATED = "lot_updated"
    LOT_DISAPPEARED = "lot_disappeared"
    SNAPSHOT_INITIALIZED = "snapshot_initialized"
    INCOME_RECEIVED = "income_received"
    EXPENSE_RECORDED = "expense_recorded"
    BALANCE_REFILLED = "balance_refilled"
    BALANCE_WITHDRAWN = "balance_withdrawn"
    ITEM_PURCHASED = "item_purchased"
    ITEM_SOLD = "item_sold"
    MONEY_TRANSFERRED = "money_transferred"
    MONEY_RECEIVED = "money_received"
    INTERNAL_PURCHASE = "internal_purchase"
    HOLD_CLAIMED = "hold_claimed"
    PAYOUT_REQUESTED = "payout_requested"
    AUTO_PAYMENT_TRIGGERED = "auto_payment_triggered"
    BALANCE_EXCHANGED = "balance_exchanged"
    TRANSFER_HELD = "transfer_held"
    TRANSFER_CANCELLED = "transfer_cancelled"
    INVOICE_CREATED = "invoice_created"
    INVOICE_PAID = "invoice_paid"
    INVOICE_EXPIRED = "invoice_expired"
    GUARANTEE_EXPIRING = "guarantee_expiring"
    ACCOUNT_INVALID = "account_invalid"
    DISPUTE_OPENED = "dispute_opened"
    CLAIM_FILED = "claim_filed"
    LOT_RESERVED = "lot_reserved"
    RESERVE_EXPIRED = "reserve_expired"
    PURCHASE_CONFIRMED = "purchase_confirmed"
    PURCHASE_CANCELLED = "purchase_cancelled"
    DEAL_DETECTED = "deal_detected"
    PRICE_VS_AI_CHANGED = "price_vs_ai_changed"
    INVENTORY_REVALUED = "inventory_revalued"
    DISCOUNT_REQUESTED = "discount_requested"
    DISCOUNT_APPROVED = "discount_approved"
    DISCOUNT_DECLINED = "discount_declined"
    NEW_CONVERSATION = "new_conversation"
    NEW_MESSAGE = "new_message"
    RATING_CHANGED = "rating_changed"
    MARKET_NOTIFICATION_RECEIVED = "market_notification_received"
    FORUM_NOTIFICATION_RECEIVED = "forum_notification_received"


def make_event_id(
    aggregate_id: AggregateId,
    event_type: EventType,
    content_hash: str,
    poll_epoch: int,
) -> uuid.UUID:
    """Deterministic id — same logical event always hashes to the same UUID."""
    key = f"{aggregate_id}|{event_type.value}|{content_hash}|{poll_epoch}"
    return uuid.uuid5(_EVENT_NS, key)


class DomainEvent(BaseModel):
    """Append-only event. `seq` is 0 until the log assigns a gapless value.

    `frozen=True`/`extra="ignore"` on `model_config` is inherited by every
    concrete subclass — a boundary object (built from untyped upstream payloads,
    persisted, replayed, delivered externally) is exactly the case pydantic beats
    a dataclass: validation-at-construction plus tolerant schema evolution.
    `EVENT_TYPE` is set per subclass; `build()` is the one constructor the
    differ/sources use, so determinism lives in one place.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    EVENT_TYPE: ClassVar[EventType]

    event_id: uuid.UUID
    aggregate_id: AggregateId
    occurred_at: datetime
    content_hash: str = ""
    schema_version: int = 1
    seq: int = 0
    payload: dict[str, object] = Field(default_factory=dict)
    # Set when a base event is reconstructed generically from storage (concrete
    # EVENT_TYPE is lost on a DB round-trip). Aliased to `_event_type` so every
    # existing call site keeps working — pydantic v2 would otherwise treat a
    # leading-underscore field as a PrivateAttr and reject it as a ctor kwarg.
    event_type_override: EventType | None = Field(default=None, alias="_event_type")

    @property
    def event_type(self) -> EventType:
        if self.event_type_override is not None:
            return self.event_type_override
        return type(self).EVENT_TYPE

    @classmethod
    def build(
        cls,
        *,
        aggregate_id: AggregateId,
        occurred_at: datetime,
        content_hash: str,
        poll_epoch: int,
        payload: dict[str, object] | None = None,
        **extra: object,
    ) -> Self:
        """Construct with a deterministic `event_id` derived from the dedup key."""
        return cls(
            event_id=make_event_id(aggregate_id, cls.EVENT_TYPE, content_hash, poll_epoch),
            aggregate_id=aggregate_id,
            occurred_at=occurred_at,
            content_hash=content_hash,
            payload=payload or {},
            **extra,  # type: ignore[arg-type]
        )

    def with_seq(self, seq: int) -> Self:
        """Return a copy stamped with the gapless `seq` assigned at append."""
        return self.model_copy(update={"seq": seq})
