"""Webhook HTTP transport — re-exported from the framework-agnostic `webhook_engine`.

Kept as a thin adapter module (not deleted) so `lzt_eventus.delivery.transport` stays
a stable import path for existing callers/tests (e.g. `engine.py`'s
`HttpxWebhookTransport` construction, the e2e tests' `RecordingWebhookTransport`).
"""

from __future__ import annotations

from webhook_engine.transport import (
    BaseWebhookTransport,
    HttpxWebhookTransport,
    RecordedCall,
    RecordingWebhookTransport,
)

__all__ = [
    "BaseWebhookTransport",
    "HttpxWebhookTransport",
    "RecordedCall",
    "RecordingWebhookTransport",
]
