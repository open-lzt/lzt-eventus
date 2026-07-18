"""Subscription domain model — the frozen value object repos store and return.

Secrets are kept as **hashes** here (the plaintext is shown once at creation and
never persisted): `secret_hash` for the webhook HMAC key, `stream_token_hash` for
the ws/sse stream token.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NewType

from lzt_eventus.delivery.subscription_ctx import (
    PollingCtx,
    SseCtx,
    SubscriptionCtx,
    WebhookCtx,
    WebSocketCtx,
)
from lzt_eventus.delivery.subscription_scope import NoScope, SubscriptionScope
from lzt_eventus.events.base import EventType

SubscriptionId = NewType("SubscriptionId", str)


class TransportKind(StrEnum):
    WEBHOOK = "webhook"
    WEBSOCKET = "websocket"
    SSE = "sse"
    POLLING = "polling"  # no push — client calls GET /events/pending against this sink


@dataclass(frozen=True, slots=True)
class Subscription:
    subscription_id: SubscriptionId
    transport: TransportKind
    endpoint: str
    event_types: frozenset[EventType]
    created_at: datetime
    ctx: SubscriptionCtx
    scope: SubscriptionScope = field(default_factory=NoScope)
    secret: str | None = None  # webhook HMAC key — engine signs with it (retrievable)
    stream_token_hash: str | None = None  # ws/sse — client-presented, only the hash is stored
    active: bool = True

    def consumer_name(self) -> str:
        """Cursor key in `consumer_cursor` — a sink is just another consumer."""
        return f"sink:{self.subscription_id}"


_DEFAULT_CTX_BY_TRANSPORT: dict[
    TransportKind, type[WebhookCtx | WebSocketCtx | SseCtx | PollingCtx]
] = {
    TransportKind.WEBHOOK: WebhookCtx,
    TransportKind.WEBSOCKET: WebSocketCtx,
    TransportKind.SSE: SseCtx,
    TransportKind.POLLING: PollingCtx,
}


def default_ctx_for(transport: TransportKind) -> SubscriptionCtx:
    """The transport-appropriate empty context — used when a caller omits `ctx`."""
    return _DEFAULT_CTX_BY_TRANSPORT[transport]()
