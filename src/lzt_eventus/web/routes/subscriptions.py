"""Subscription management routes — thin handlers, admin-key gated, POST/GET only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from lzt_eventus.web.schemas.dtos import (
    SubscriptionCreate,
    SubscriptionOut,
    SubscriptionRef,
    SubscriptionUpdate,
    TestResult,
)
from lzt_eventus.web.schemas.envelopes import DataResponse, Page, Success
from lzt_eventus.web.shared.deps import AdminDep, PageDep, SubscriptionServiceDep

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.post("/create")
async def create(
    data: SubscriptionCreate, svc: SubscriptionServiceDep, _: AdminDep
) -> DataResponse[SubscriptionOut]:
    result = await svc.register(data)
    return DataResponse(
        data=SubscriptionOut.of(
            result.subscription, secret=result.secret, stream_token=result.stream_token
        )
    )


@router.get("/list")
async def list_subscriptions(
    svc: SubscriptionServiceDep,
    page: PageDep,
    _: AdminDep,
    active_only: Annotated[bool, Query()] = False,
) -> Page[SubscriptionOut]:
    rows, total = await svc.list_(limit=page.limit, offset=page.offset, active_only=active_only)
    return Page(
        items=[SubscriptionOut.of(s) for s in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/get")
async def get_subscription(
    subscription_id: Annotated[str, Query()], svc: SubscriptionServiceDep, _: AdminDep
) -> DataResponse[SubscriptionOut]:
    return DataResponse(data=SubscriptionOut.of(await svc.get(subscription_id)))


@router.post("/update")
async def update_subscription(
    data: SubscriptionUpdate, svc: SubscriptionServiceDep, _: AdminDep
) -> DataResponse[SubscriptionOut]:
    return DataResponse(data=SubscriptionOut.of(await svc.update(data)))


@router.post("/deactivate")
async def deactivate_subscription(
    data: SubscriptionRef, svc: SubscriptionServiceDep, _: AdminDep
) -> Success:
    await svc.deactivate(data.subscription_id)
    return Success()


@router.post("/test")
async def test_subscription(
    data: SubscriptionRef, svc: SubscriptionServiceDep, _: AdminDep
) -> DataResponse[TestResult]:
    sub = await svc.get(data.subscription_id)
    return DataResponse(
        data=TestResult(delivered=True, detail=f"{sub.transport.value} active={sub.active}")
    )
