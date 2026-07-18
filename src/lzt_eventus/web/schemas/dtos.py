"""Subscription + token-account request/response DTOs (pydantic, boundary-validated)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from lzt_eventus.account.token_account import Alias, TokenAccount
from lzt_eventus.delivery.subscription import Subscription, TransportKind
from lzt_eventus.delivery.subscription_ctx import SubscriptionCtx
from lzt_eventus.delivery.subscription_scope import NoScope, SubscriptionScope
from lzt_eventus.web.base.schema import BaseSchema


class SubscriptionCreate(BaseSchema):
    transport: TransportKind
    endpoint: str = Field(min_length=1)
    event_types: list[str] = Field(min_length=1)
    # Per-transport knobs (e.g. PollingCtx.poll_delay_seconds). Omit to get the
    # transport's empty default — the service fills it in and also rejects a
    # `ctx.kind` that doesn't match `transport`.
    ctx: SubscriptionCtx | None = None
    # What this subscription is scoped to — NoScope (everything requested),
    # CategoryScope (catalog events for one category), or AccountScope (one
    # account's per-account events, e.g. rating). The service rejects a scope
    # that can never match any of `event_types`.
    scope: SubscriptionScope = Field(default_factory=NoScope)
    # Webhook/polling only: replay the whole retained backlog on creation. Default
    # false — a new subscription receives events from its creation point forward.
    backfill: bool = False


class SubscriptionUpdate(BaseSchema):
    subscription_id: str
    event_types: list[str] | None = None
    scope: SubscriptionScope | None = None
    active: bool | None = None


class SubscriptionRef(BaseSchema):
    subscription_id: str


class SubscriptionOut(BaseSchema):
    subscription_id: str
    transport: TransportKind
    endpoint: str
    event_types: list[str]
    scope: SubscriptionScope
    ctx: SubscriptionCtx
    active: bool
    created_at: datetime
    # Returned ONLY on create (the one-time plaintext); redacted on list/get.
    secret: str | None = None
    stream_token: str | None = None

    @classmethod
    def of(
        cls,
        sub: Subscription,
        *,
        secret: str | None = None,
        stream_token: str | None = None,
    ) -> SubscriptionOut:
        return cls(
            subscription_id=str(sub.subscription_id),
            transport=sub.transport,
            endpoint=sub.endpoint,
            event_types=sorted(et.value for et in sub.event_types),
            scope=sub.scope,
            ctx=sub.ctx,
            active=sub.active,
            created_at=sub.created_at,
            secret=secret,
            stream_token=stream_token,
        )


class TokenAccountCreate(BaseSchema):
    token: str = Field(min_length=1)
    alias: str = Field(min_length=1, max_length=128)
    metadata: dict[str, str] = Field(default_factory=dict)
    categories: list[str] = Field(default_factory=list)


class TokenAccountUpdate(BaseSchema):
    account_id: str
    metadata: dict[str, str] | None = None
    active: bool | None = None


class AliasAdd(BaseSchema):
    account_id: str
    alias: str = Field(min_length=1, max_length=128)


class AliasRef(BaseSchema):
    alias: str


class TokenAccountRef(BaseSchema):
    account_id: str


class TokenAccountOut(BaseSchema):
    account_id: str
    metadata: dict[str, str]
    categories: list[str]
    active: bool
    created_at: datetime
    # Returned ONLY on GET-by-alias (the raw credential); redacted everywhere else.
    token: str | None = None

    @classmethod
    def of(cls, account: TokenAccount, *, token: str | None = None) -> TokenAccountOut:
        return cls(
            account_id=str(account.account_id),
            metadata=dict(account.metadata),
            categories=[c.value for c in account.categories],
            active=account.active,
            created_at=account.created_at,
            token=token,
        )


class AliasOut(BaseSchema):
    alias: str
    account_id: str
    is_primary: bool
    created_at: datetime

    @classmethod
    def of(cls, alias: Alias) -> AliasOut:
        return cls(
            alias=alias.alias,
            account_id=str(alias.account_id),
            is_primary=alias.is_primary,
            created_at=alias.created_at,
        )


class TestResult(BaseSchema):
    delivered: bool
    detail: str


class WsAuth(BaseSchema):
    """First WebSocket frame on `/streams/ws` — authenticate + set resume point."""

    subscription_id: str
    token: str
    last_seq: int = Field(default=0, ge=0)
