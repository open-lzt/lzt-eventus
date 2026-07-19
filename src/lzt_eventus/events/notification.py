"""Notification events (3-E) + the `content_type` → event dispatch.

`MarketNotificationReceived`/`ForumNotificationReceived` are the catch-all emitted
by `NotificationsSource` when a notification's `content_type` doesn't match one of
the more specific sub-events (`LOT_RESERVED`, `PURCHASE_CONFIRMED`, `DISPUTE_OPENED`,
`CLAIM_FILED` — `DISCOUNT_REQUESTED` still has no concrete class) — never a
dead-code stub, always reachable.

`parse_notification` owns the dispatch so the source stays a thin poll loop: it
maps `content_type` to the concrete event next to the event definitions, and falls
back to the feed's marker event for anything unmatched (the reserved `EventType`
catalog outruns the concrete classes, and unknown upstream types are an open set).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_core import ErrorDetails
from pylzt.models.notification import Notification

from lzt_eventus.errors import EngineError
from lzt_eventus.events.account import ClaimFiled, DisputeOpened
from lzt_eventus.events.base import AggregateId, DomainEvent, EventType
from lzt_eventus.events.lot import LotReserved, PurchaseConfirmed


class MarketNotificationReceived(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.MARKET_NOTIFICATION_RECEIVED
    notification_id: int
    content_type: str
    created_at: datetime


class ForumNotificationReceived(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.FORUM_NOTIFICATION_RECEIVED
    notification_id: int
    content_type: str
    created_at: datetime


_EventKwargsBuilder = Callable[[Notification], dict[str, object]]


class NotificationExtraInvalid(EngineError):
    """A notification's `extra` payload had a field present but not coercible to `int`."""

    def __init__(self, notification_id: int, errors: list[ErrorDetails]) -> None:
        self.notification_id = notification_id
        self.errors = errors
        super().__init__(f"notification {notification_id}: invalid extra payload")


class NotificationExtra(BaseModel):
    """Typed view of a notification's untyped `extra` catch-all.

    Every field defaults to `None` so an absent key is tolerated; a present-but-
    uncoercible value fails validation instead (see `_parse_extra`).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    item_id: int | None = None
    claim_id: int | None = None
    buyer_id: int | None = None


def _parse_extra(notification: Notification) -> NotificationExtra:
    try:
        return NotificationExtra.model_validate(notification.extra)
    except ValidationError as exc:
        raise NotificationExtraInvalid(notification.notification_id, exc.errors()) from exc


def _field_or_default(extra: NotificationExtra, field: str, *, default: int | None) -> int | None:
    # `field in model_fields_set` distinguishes "absent from extra" (use default)
    # from "present in extra, even as an explicit null" (use the parsed value,
    # never the default) — matches the pre-migration `dict.get(key, default)` semantics.
    if field not in extra.model_fields_set:
        return default
    return getattr(extra, field)  # type: ignore[no-any-return]


def _claim_kwargs(notification: Notification) -> dict[str, object]:
    extra = _parse_extra(notification)
    return {
        "claim_id": _field_or_default(extra, "claim_id", default=notification.notification_id),
        "item_id": _field_or_default(extra, "item_id", default=None),
    }


def _item_buyer_kwargs(notification: Notification) -> dict[str, object]:
    extra = _parse_extra(notification)
    return {
        "item_id": _field_or_default(extra, "item_id", default=notification.notification_id),
        "buyer_id": _field_or_default(extra, "buyer_id", default=None),
    }


# content_type -> (event class, kwargs builder); a new sub-event is a dict entry,
# never a branch in parse_notification. Literal values are UNVERIFIED
# (api-events-sources.md rows 10/11/15) — revisit once a live Forum token surfaces
# the real vocabulary.
_CONTENT_TYPE_EVENTS: Final[dict[str, tuple[type[DomainEvent], _EventKwargsBuilder]]] = {
    "market_dispute_opened": (DisputeOpened, _claim_kwargs),
    "market_claim_filed": (ClaimFiled, _claim_kwargs),
    "market_lot_reserved": (LotReserved, _item_buyer_kwargs),
    "market_purchase_confirmed": (PurchaseConfirmed, _item_buyer_kwargs),
}


def parse_notification(
    notification: Notification,
    *,
    kind: str,
    fallback_cls: type[DomainEvent],
    content_hash: str,
    poll_epoch: int,
) -> DomainEvent:
    """Dispatch a raw Forum notification to its typed event by `content_type`.

    Unmatched `content_type`s fall back to `fallback_cls` (the feed's marker event)
    rather than raising — see the module docstring.
    """
    aggregate_id = AggregateId(f"notif:{kind}:{notification.notification_id}")
    occurred_at = datetime.fromtimestamp(notification.created_at, tz=UTC)
    entry = _CONTENT_TYPE_EVENTS.get(notification.content_type)
    if entry is not None:
        event_cls, kwargs_builder = entry
        return event_cls.build(
            aggregate_id=aggregate_id,
            occurred_at=occurred_at,
            content_hash=content_hash,
            poll_epoch=poll_epoch,
            # dict spread vs build()'s mixed keyword/**extra signature
            **kwargs_builder(notification),  # type: ignore[arg-type]
        )
    return fallback_cls.build(
        aggregate_id=aggregate_id,
        occurred_at=occurred_at,
        content_hash=content_hash,
        poll_epoch=poll_epoch,
        notification_id=notification.notification_id,
        content_type=notification.content_type,
        created_at=occurred_at,
    )
