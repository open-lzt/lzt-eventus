"""Token-account management routes — thin handlers, admin-key gated, POST/GET only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from lzt_eventus.web.schemas.dtos import (
    AliasAdd,
    AliasOut,
    TokenAccountCreate,
    TokenAccountOut,
    TokenAccountRef,
    TokenAccountUpdate,
)
from lzt_eventus.web.schemas.envelopes import DataResponse, Page, Success
from lzt_eventus.web.shared.deps import AdminDep, PageDep, TokenAccountServiceDep

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.post("/register")
async def register(
    data: TokenAccountCreate, svc: TokenAccountServiceDep, _: AdminDep
) -> DataResponse[TokenAccountOut]:
    result = await svc.register(data)
    return DataResponse(data=TokenAccountOut.of(result.account))


@router.get("/by-alias")
async def get_by_alias(
    alias: Annotated[str, Query()], svc: TokenAccountServiceDep, _: AdminDep
) -> DataResponse[TokenAccountOut]:
    account, token = await svc.get_token_by_alias(alias)
    return DataResponse(data=TokenAccountOut.of(account, token=token))


@router.post("/metadata")
async def update_metadata(
    data: TokenAccountUpdate, svc: TokenAccountServiceDep, _: AdminDep
) -> DataResponse[TokenAccountOut]:
    return DataResponse(data=TokenAccountOut.of(await svc.update_metadata(data)))


@router.post("/aliases/add")
async def add_alias(
    data: AliasAdd, svc: TokenAccountServiceDep, _: AdminDep
) -> DataResponse[AliasOut]:
    return DataResponse(data=AliasOut.of(await svc.add_alias(data)))


@router.get("/aliases")
async def list_aliases(
    account_id: Annotated[str, Query()], svc: TokenAccountServiceDep, _: AdminDep
) -> Page[AliasOut]:
    rows = await svc.list_aliases(account_id)
    return Page(items=[AliasOut.of(a) for a in rows], total=len(rows), limit=len(rows), offset=0)


@router.get("/list")
async def list_accounts(
    svc: TokenAccountServiceDep,
    page: PageDep,
    _: AdminDep,
    active_only: Annotated[bool, Query()] = False,
) -> Page[TokenAccountOut]:
    rows, total = await svc.list_(limit=page.limit, offset=page.offset, active_only=active_only)
    return Page(
        items=[TokenAccountOut.of(a) for a in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/deactivate")
async def deactivate(data: TokenAccountRef, svc: TokenAccountServiceDep, _: AdminDep) -> Success:
    await svc.deactivate(data.account_id)
    return Success()
