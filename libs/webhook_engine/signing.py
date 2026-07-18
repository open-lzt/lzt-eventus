"""HMAC-SHA256 signing of the outbound webhook body.

The engine signs each delivery with the subscription's retrievable `secret`; the
receiver re-signs the raw body it got and constant-time compares. Header names are
shared so a consumer can verify with `verify_webhook` symmetrically.
"""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-LZT-Signature"
EVENT_ID_HEADER = "X-LZT-Event-Id"
EVENT_TYPE_HEADER = "X-LZT-Event-Type"
IDEMPOTENCY_HEADER = "Idempotency-Key"

_SCHEME = "sha256="


def sign_webhook(secret: str, body: bytes) -> str:
    """Hex HMAC-SHA256 of `body` under `secret` (the value, sans scheme prefix)."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def signature_header(secret: str, body: bytes) -> str:
    """The full `X-LZT-Signature` value, e.g. `sha256=<hex>`."""
    return f"{_SCHEME}{sign_webhook(secret, body)}"


def verify_webhook(secret: str, body: bytes, presented: str | None) -> bool:
    """Constant-time check that `presented` (`sha256=<hex>`) signs `body`."""
    if not presented or not presented.startswith(_SCHEME):
        return False
    expected = sign_webhook(secret, body)
    return hmac.compare_digest(expected, presented[len(_SCHEME) :])
