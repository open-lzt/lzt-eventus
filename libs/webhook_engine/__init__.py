"""webhook_engine — framework-agnostic outbound webhook delivery.

Signs, retries (exponential backoff), and reconciles a set of webhook destinations
into live `WebhookSink`s. Zero coupling to any host application's event/bus/config
shape — a host wires its own domain events and subscription store through
`WebhookTarget` + the DTOs in this package.

Public surface: `WebhookEngineConfig`, `BaseWebhookTransport` (+ httpx and recording
impls), `WebhookSink`, `WebhookDispatcher` + `WebhookTarget`, `verify_webhook`.
"""

from __future__ import annotations

from webhook_engine.config import WebhookEngineConfig
from webhook_engine.dispatcher import WebhookDispatcher, WebhookTarget
from webhook_engine.errors import (
    DeliveryError,
    WebhookDeliveryError,
    WebhookTransportError,
)
from webhook_engine.signing import sign_webhook, signature_header, verify_webhook
from webhook_engine.sink import WebhookSink
from webhook_engine.transport import (
    BaseWebhookTransport,
    HttpxWebhookTransport,
    RecordingWebhookTransport,
)

__all__ = [
    "BaseWebhookTransport",
    "DeliveryError",
    "HttpxWebhookTransport",
    "RecordingWebhookTransport",
    "WebhookDeliveryError",
    "WebhookDispatcher",
    "WebhookEngineConfig",
    "WebhookSink",
    "WebhookTarget",
    "WebhookTransportError",
    "sign_webhook",
    "signature_header",
    "verify_webhook",
]
