"""HMAC-SHA256 signing of the outbound webhook body — re-exported from `webhook_engine`.

Kept as a thin adapter module (not deleted) so `lzt_eventus.delivery.signing` stays
a stable import path for existing callers/tests.
"""

from __future__ import annotations

from webhook_engine.signing import (
    EVENT_ID_HEADER,
    EVENT_TYPE_HEADER,
    IDEMPOTENCY_HEADER,
    SIGNATURE_HEADER,
    sign_webhook,
    signature_header,
    verify_webhook,
)

__all__ = [
    "EVENT_ID_HEADER",
    "EVENT_TYPE_HEADER",
    "IDEMPOTENCY_HEADER",
    "SIGNATURE_HEADER",
    "sign_webhook",
    "signature_header",
    "verify_webhook",
]
