"""FastAPI dependencies — typed `Annotated` aliases, services built here not in handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query, Request

from lzt_eventus.web.services.polling import PollingService
from lzt_eventus.web.services.subscriptions import SubscriptionAdminService
from lzt_eventus.web.services.token_accounts import TokenAccountAdminService
from lzt_eventus.web.shared.handle import EngineHandle
from lzt_eventus.web.shared.security import extract_bearer, verify_admin_key


def get_handle(request: Request) -> EngineHandle:
    return request.app.state.handle  # type: ignore[no-any-return]


HandleDep = Annotated[EngineHandle, Depends(get_handle)]


async def require_admin(
    handle: HandleDep,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    key = extract_bearer(authorization, x_api_key)
    verify_admin_key(key, handle.config.admin_api_key.get_secret_value())


AdminDep = Annotated[None, Depends(require_admin)]


def get_subscription_service(handle: HandleDep) -> SubscriptionAdminService:
    return SubscriptionAdminService(
        handle.subscriptions,
        handle.cursors,
        handle.event_log,
        token_accounts=handle.token_accounts,
    )


SubscriptionServiceDep = Annotated[SubscriptionAdminService, Depends(get_subscription_service)]


def get_token_account_service(handle: HandleDep) -> TokenAccountAdminService:
    return TokenAccountAdminService(
        handle.token_accounts, handle.account_reconciler, handle.secret_box, handle.config
    )


TokenAccountServiceDep = Annotated[TokenAccountAdminService, Depends(get_token_account_service)]


def get_polling_service(handle: HandleDep) -> PollingService:
    return PollingService(handle.event_log, handle.cursors)


PollingServiceDep = Annotated[PollingService, Depends(get_polling_service)]


@dataclass(frozen=True, slots=True)
class Pagination:
    limit: int
    offset: int


def pagination(
    # Upper bound is enforced by `LimitValidationMiddleware` before this dependency runs.
    limit: Annotated[int, Query(ge=1)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


PageDep = Annotated[Pagination, Depends(pagination)]
