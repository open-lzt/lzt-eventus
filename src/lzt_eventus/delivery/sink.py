"""`WebhookSink` — a subscription rendered as a bus consumer (`BaseConsumer`).

Thin adapter: wraps a framework-agnostic `webhook_engine.WebhookSink` (which owns
the actual sign/retry/backoff delivery) with the lzt_eventus-specific bus metadata —
`name` (its `sink:<id>` cursor key) and `subscriptions` (event-type/filter interest).
"""

from __future__ import annotations

from lzt_eventus.codecs.json import canonical_bytes, event_envelope
from lzt_eventus.consumers.consumer import BaseConsumer, BaseSubscription
from lzt_eventus.delivery.errors import WebhookDeliveryError
from lzt_eventus.delivery.subscription import Subscription
from lzt_eventus.delivery.subscription_scope import to_filters
from lzt_eventus.events.base import DomainEvent
from webhook_engine.errors import WebhookDeliveryError as _LibWebhookDeliveryError
from webhook_engine.sink import WebhookSink as LibWebhookSink

# (endpoint, secret, event_types, filters, active) — when any changes the cached
# sink is rebuilt so a live edit takes effect without a daemon restart.
SinkSignature = tuple[str, str | None, frozenset[str], tuple[tuple[str, str], ...], bool]


def sink_signature(sub: Subscription) -> SinkSignature:
    return (
        sub.endpoint,
        sub.secret,
        frozenset(et.value for et in sub.event_types),
        tuple(sorted(to_filters(sub.scope).items())),
        sub.active,
    )


class WebhookSink(BaseConsumer):
    def __init__(self, sub: Subscription, *, delegate: LibWebhookSink) -> None:
        self._sub = sub
        self._delegate = delegate
        self._signature = sink_signature(sub)
        self.name = sub.consumer_name()
        self.subscriptions = [
            BaseSubscription(event_types=frozenset(sub.event_types), filters=to_filters(sub.scope))
        ]

    @property
    def signature(self) -> SinkSignature:
        return self._signature

    async def handle(self, event: DomainEvent) -> None:
        body = canonical_bytes(event_envelope(event))
        try:
            await self._delegate.deliver(
                event_id=str(event.event_id), event_type=event.event_type.value, body=body
            )
        except _LibWebhookDeliveryError as exc:
            raise WebhookDeliveryError(
                subscription_id=str(self._sub.subscription_id),
                endpoint=exc.endpoint,
                reason=exc.reason,
            ) from exc
