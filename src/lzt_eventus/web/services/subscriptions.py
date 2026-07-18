"""`SubscriptionAdminService` — register / list / get / update / deactivate / test.

All management logic lives here; routes stay thin. Mints the per-transport secret
(webhook HMAC key, retrievable) or stream token (ws/sse, stored hashed), validates
`event_types` against the real `EventType` catalog, and tears down a deactivated
subscription's `sink:<id>` cursor so it can't pin the retention watermark.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import replace
from uuid import uuid4

from lzt_eventus.account.repo import BaseTokenAccountRepo
from lzt_eventus.cursor.base import BaseCursorStore
from lzt_eventus.delivery.repo import BaseSubscriptionRepo
from lzt_eventus.delivery.subscription import (
    Subscription,
    SubscriptionId,
    TransportKind,
    default_ctx_for,
)
from lzt_eventus.delivery.subscription_scope import (
    AccountScope,
    SubscriptionScope,
    unsupported_event_types,
)
from lzt_eventus.errors import CursorConflict
from lzt_eventus.events.base import EventType
from lzt_eventus.lib.clock import Clock, RealClock
from lzt_eventus.log.base import BaseEventLog
from lzt_eventus.web.base.errors import (
    AliasNotFound,
    SubscriptionCtxMismatch,
    SubscriptionNotFound,
    SubscriptionScopeMismatch,
    UnsafeWebhookEndpoint,
)
from lzt_eventus.web.base.service import BaseService
from lzt_eventus.web.schemas.dtos import SubscriptionCreate, SubscriptionUpdate
from lzt_eventus.web.shared.event_types import parse_event_types
from lzt_eventus.web.shared.security import hash_stream_token
from webhook_engine.errors import UnsafeWebhookUrl
from webhook_engine.url_safety import assert_safe_webhook_url


class RegisterResult:
    """The created subscription plus the one-time plaintext secrets (shown once)."""

    __slots__ = ("secret", "stream_token", "subscription")

    def __init__(
        self, subscription: Subscription, secret: str | None, stream_token: str | None
    ) -> None:
        self.subscription = subscription
        self.secret = secret
        self.stream_token = stream_token


class SubscriptionAdminService(BaseService):
    def __init__(
        self,
        repo: BaseSubscriptionRepo,
        cursors: BaseCursorStore,
        event_log: BaseEventLog,
        *,
        token_accounts: BaseTokenAccountRepo | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._repo = repo
        self._cursors = cursors
        self._event_log = event_log
        self._token_accounts = token_accounts
        self._clock = clock or RealClock()

    async def _resolve_scope(
        self, scope: SubscriptionScope, event_types: frozenset[EventType]
    ) -> SubscriptionScope:
        bad = unsupported_event_types(scope, event_types)
        if bad:
            raise SubscriptionScopeMismatch(
                scope_kind=scope.kind, unsupported=sorted(et.value for et in bad)
            )
        # `token_accounts=None` (no reconciler wired, e.g. a bare management-only
        # deployment) means account-scoped subscriptions aren't supported here —
        # fail loud rather than silently accepting an alias no source ever tags.
        if isinstance(scope, AccountScope) and (
            self._token_accounts is None
            or not await self._token_accounts.alias_exists(scope.account_alias)
        ):
            raise AliasNotFound(alias=scope.account_alias)
        return scope

    async def register(self, spec: SubscriptionCreate) -> RegisterResult:
        event_types = parse_event_types(spec.event_types)
        scope = await self._resolve_scope(spec.scope, event_types)
        if spec.ctx is None:
            ctx = default_ctx_for(spec.transport)
        elif spec.ctx.kind != spec.transport.value:
            raise SubscriptionCtxMismatch(transport=spec.transport, ctx_kind=spec.ctx.kind)
        else:
            ctx = spec.ctx
        secret: str | None = None
        stream_token: str | None = None
        stream_hash: str | None = None
        if spec.transport is TransportKind.WEBHOOK:
            try:
                assert_safe_webhook_url(spec.endpoint)
            except UnsafeWebhookUrl as exc:
                raise UnsafeWebhookEndpoint(endpoint=exc.url, reason=exc.reason) from exc
            secret = secrets.token_urlsafe(32)
        elif spec.transport is TransportKind.POLLING:
            pass  # pull-only, already admin-key gated — no push secret/stream token to mint
        else:
            stream_token = secrets.token_urlsafe(32)
            stream_hash = hash_stream_token(stream_token)
        sub = Subscription(
            subscription_id=SubscriptionId(uuid4().hex),
            transport=spec.transport,
            endpoint=spec.endpoint,
            event_types=event_types,
            created_at=self._clock.now(),
            ctx=ctx,
            scope=scope,
            secret=secret,
            stream_token_hash=stream_hash,
            active=True,
        )
        # Seed the sink cursor to the log head BEFORE the repo add, so a webhook/source gets
        # events from now forward (no backlog flood) and the dispatcher can never see
        # the subscription with an un-seeded cursor=0 in the gap. `backfill` opts into
        # full replay by leaving the cursor at 0.
        if spec.transport in (TransportKind.WEBHOOK, TransportKind.POLLING) and not spec.backfill:
            head = await self._event_log.max_seq()
            if head > 0:
                state = await self._cursors.get(sub.consumer_name())
                await self._cursors.commit(sub.consumer_name(), head, state.version)
        await self._repo.add(sub)
        return RegisterResult(sub, secret, stream_token)

    async def get(self, subscription_id: str) -> Subscription:
        sub = await self._repo.get(SubscriptionId(subscription_id))
        if sub is None:
            raise SubscriptionNotFound(subscription_id=subscription_id)
        return sub

    async def list_(
        self, *, limit: int, offset: int, active_only: bool
    ) -> tuple[Sequence[Subscription], int]:
        rows = await self._repo.list(limit=limit, offset=offset, active_only=active_only)
        total = await self._repo.count(active_only=active_only)
        return rows, total

    async def update(self, spec: SubscriptionUpdate) -> Subscription:
        current = await self.get(spec.subscription_id)
        changes: dict[str, object] = {}
        if spec.event_types is not None:
            changes["event_types"] = parse_event_types(spec.event_types)
        if spec.scope is not None:
            changes["scope"] = await self._resolve_scope(
                spec.scope, changes.get("event_types", current.event_types)  # type: ignore[arg-type]
            )
        if spec.active is not None:
            changes["active"] = spec.active
        updated = replace(current, **changes)  # type: ignore[arg-type]
        return await self._repo.replace(updated)

    async def deactivate(self, subscription_id: str) -> None:
        sub = await self.get(subscription_id)
        await self._repo.replace(replace(sub, active=False))
        # Drop the sink cursor so a dead subscription never pins the watermark.
        with suppress(CursorConflict):
            await self._cursors.delete(sub.consumer_name())
