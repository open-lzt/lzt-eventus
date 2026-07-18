"""Per-transport subscription context — the extra knobs one transport kind needs
beyond the fields every `Subscription` shares (e.g. the polling long-wait).

A discriminated union on `kind` (not a bare `Subscription.transport` re-check) so
pydantic parses the right variant straight off raw JSON — the caller never
pre-declares a type, `ctx: SubscriptionCtx` in a request body is enough.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class WebhookCtx(BaseModel):
    kind: Literal["webhook"] = "webhook"


class WebSocketCtx(BaseModel):
    kind: Literal["websocket"] = "websocket"


class SseCtx(BaseModel):
    kind: Literal["sse"] = "sse"


class PollingCtx(BaseModel):
    kind: Literal["polling"] = "polling"
    # Minimum wait `PollingService.peek` enforces on an empty batch before
    # returning (long-poll emulation) — cuts client hammering on quiet
    # subscriptions. 0 keeps today's immediate-return behavior.
    poll_delay_seconds: float = Field(default=0.0, ge=0, le=300)


SubscriptionCtx = Annotated[
    WebhookCtx | WebSocketCtx | SseCtx | PollingCtx,
    Field(discriminator="kind"),
]
