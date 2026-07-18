"""Webhook-push delivery: subscriptions as cursor-bearing sinks over the durable log.

Public surface: `WebhookDelivery` (the facade the engine wires), `BaseWebhookTransport`
(+ httpx and recording impls), and `verify_webhook` for receivers.
"""

from __future__ import annotations

from lzt_eventus.delivery.delivery import WebhookDelivery
from lzt_eventus.delivery.errors import (
    DeliveryError,
    WebhookDeliveryError,
    WebhookTransportError,
)
from lzt_eventus.delivery.signing import sign_webhook, signature_header, verify_webhook
from lzt_eventus.delivery.transport import (
    BaseWebhookTransport,
    HttpxWebhookTransport,
    RecordingWebhookTransport,
)

__all__ = [
    "BaseWebhookTransport",
    "DeliveryError",
    "HttpxWebhookTransport",
    "RecordingWebhookTransport",
    "WebhookDelivery",
    "WebhookDeliveryError",
    "WebhookTransportError",
    "sign_webhook",
    "signature_header",
    "verify_webhook",
]
