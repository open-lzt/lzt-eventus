"""Typed monitoring scope for a subscription — replaces the untyped
`filters: dict[str, str]` + `account_alias: str | None` pair at the API boundary.

The event bus itself stays payload-agnostic (`BaseSubscription.filters` in
`consumers/consumer.py` matches ANY key against `event.payload`, on purpose —
it doesn't know what "category" or "account_alias" mean). `to_filters()` is the
one place a typed scope compiles down into that generic dict, at the sink/service
boundary (`delivery/sink.py`, `web/services/{polling,streams}.py`).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pylzt.types import Category
from pydantic import BaseModel, Field

from lzt_eventus.events.base import EventType


class NoScope(BaseModel):
    kind: Literal["none"] = "none"


class CategoryScope(BaseModel):
    kind: Literal["category"] = "category"
    category: Category


class AccountScope(BaseModel):
    kind: Literal["account"] = "account"
    account_alias: str


SubscriptionScope = Annotated[
    NoScope | CategoryScope | AccountScope,
    Field(discriminator="kind"),
]

# Catalog events carry `payload["category"]` (`diff/differ.py::_category_payload`).
CATALOG_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.NEW_LOT,
        EventType.PRICE_DROPPED,
        EventType.LOT_UPDATED,
        EventType.LOT_DISAPPEARED,
        EventType.SNAPSHOT_INITIALIZED,
    }
)
# Per-account sources tag `payload["account_alias"]` (`sources/rating.py`) —
# extend as event-sources-expansion wires payments/notifications/conversations/
# guarantee onto the same convention.
ACCOUNT_EVENT_TYPES: frozenset[EventType] = frozenset({EventType.RATING_CHANGED})

_SUPPORTED_EVENT_TYPES: dict[str, frozenset[EventType] | None] = {
    "none": None,  # None == no restriction, any event_types allowed
    "category": CATALOG_EVENT_TYPES,
    "account": ACCOUNT_EVENT_TYPES,
}


def to_filters(scope: NoScope | CategoryScope | AccountScope) -> dict[str, str]:
    """Compile a typed scope down to the generic payload-match dict the bus reads."""
    if isinstance(scope, CategoryScope):
        return {"category": scope.category.value}
    if isinstance(scope, AccountScope):
        return {"account_alias": scope.account_alias}
    return {}


def unsupported_event_types(
    scope: NoScope | CategoryScope | AccountScope, event_types: frozenset[EventType]
) -> frozenset[EventType]:
    """Event types in `event_types` that `scope` can never match — empty if all OK."""
    supported = _SUPPORTED_EVENT_TYPES[scope.kind]
    if supported is None:
        return frozenset()
    return event_types - supported
