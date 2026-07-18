"""Delivery error tree — re-exports the framework-agnostic `webhook_engine` tree.

`WebhookDeliveryError` adds `subscription_id` (this engine's identity for a sink)
alongside the lib's generic `sink_id`, so existing catch sites reading
`.subscription_id` keep working untouched.
"""

from __future__ import annotations

from webhook_engine.errors import DeliveryError, WebhookTransportError
from webhook_engine.errors import WebhookDeliveryError as _LibWebhookDeliveryError

__all__ = ["DeliveryError", "WebhookDeliveryError", "WebhookTransportError"]


class WebhookDeliveryError(_LibWebhookDeliveryError):
    """Every retry was exhausted — the bus parks the event in the DLQ."""

    def __init__(self, *, subscription_id: str, endpoint: str, reason: str) -> None:
        super().__init__(sink_id=subscription_id, endpoint=endpoint, reason=reason)
        self.subscription_id = subscription_id
