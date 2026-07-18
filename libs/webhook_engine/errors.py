"""Delivery error tree — typed, carrying args (never pre-formatted text)."""

from __future__ import annotations


class DeliveryError(Exception):
    """Root of the webhook-delivery error hierarchy."""


class WebhookTransportError(DeliveryError):
    """A single POST attempt failed at the transport (timeout / connection / DNS)."""

    def __init__(self, *, url: str, reason: str) -> None:
        super().__init__(reason)
        self.url = url
        self.reason = reason


class WebhookDeliveryError(DeliveryError):
    """Every retry was exhausted — the caller should park the event (e.g. in a DLQ)."""

    def __init__(self, *, sink_id: str, endpoint: str, reason: str) -> None:
        super().__init__(reason)
        self.sink_id = sink_id
        self.endpoint = endpoint
        self.reason = reason


class UnsafeWebhookUrl(DeliveryError):
    """The URL (or one of its resolved IPs) targets a disallowed range — SSRF guard tripped."""

    def __init__(self, *, url: str, reason: str) -> None:
        super().__init__(reason)
        self.url = url
        self.reason = reason
