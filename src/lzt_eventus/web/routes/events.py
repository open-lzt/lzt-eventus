"""Polling API — pending-event catch-up over a registered `transport=polling` subscription.

An alternative to webhook/stream delivery for callers that would rather pull than
receive a push: create a subscription via `POST /subscriptions/create` with
`"transport": "polling"`, then `GET /events/pending` against its `subscription_id`.
Each subscription tracks its own cursor, so independent sources never race each
other. With `read_all=false` (default) the cursor is left untouched — the same
batch replays on retry. `read_all=true` commits the exact batch scanned in this
request; otherwise the caller confirms explicitly via `POST /events/read_events`.

The `limit` upper bound is enforced by `LimitValidationMiddleware` before this
handler ever runs (`invalid_limit` / `limit_too_large`) — routes only declare
`ge=1` here for basic input sanity + OpenAPI docs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from lzt_eventus.delivery.subscription import Subscription, TransportKind
from lzt_eventus.web.base.error_codes import ErrorCode
from lzt_eventus.web.base.errors import BadRequest
from lzt_eventus.web.schemas.events import (
    PendingEventsOut,
    ReadEventsOut,
    ReadEventsRequest,
)
from lzt_eventus.web.shared.deps import AdminDep, PollingServiceDep, SubscriptionServiceDep
from lzt_eventus.web.shared.event_types import parse_event_types

router = APIRouter(prefix="/events", tags=["events"])

_DEFAULT_LIMIT = 100


class NotAPollingSubscription(BadRequest):
    code = ErrorCode.NOT_A_POLLING_SUBSCRIPTION


async def _resolve_polling_sub(subs: SubscriptionServiceDep, subscription_id: str) -> Subscription:
    sub = await subs.get(subscription_id)
    if sub.transport is not TransportKind.POLLING:
        raise NotAPollingSubscription(subscription_id=subscription_id, transport=sub.transport)
    return sub


@router.get("/pending")
async def pending(
    svc: PollingServiceDep,
    subs: SubscriptionServiceDep,
    _: AdminDep,
    subscription_id: Annotated[str, Query()],
    event_type: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1)] = _DEFAULT_LIMIT,
    read_all: Annotated[bool, Query()] = False,
) -> PendingEventsOut:
    sub = await _resolve_polling_sub(subs, subscription_id)
    types = parse_event_types(event_type) if event_type else None
    batch = await svc.peek(sub, types, limit)
    committed = False
    if read_all and batch.next_seq > batch.last_read_seq:
        await svc.confirm(sub, batch.next_seq)
        committed = True
    return PendingEventsOut.of(subscription_id, batch, committed=committed)


@router.post("/read_events")
async def read_events(
    svc: PollingServiceDep, subs: SubscriptionServiceDep, _: AdminDep, body: ReadEventsRequest
) -> ReadEventsOut:
    sub = await _resolve_polling_sub(subs, body.subscription_id)
    last_seq = await svc.confirm(sub, body.up_to_seq)
    return ReadEventsOut(subscription_id=body.subscription_id, last_seq=last_seq)
