"""API-key verification — two distinct keys, constant-time compared.

Admin key gates the management plane; the per-subscription stream token gates the
event egress. Compromising one stream never grants management access.
"""

from __future__ import annotations

import hashlib
import hmac

from lzt_eventus.web.base.errors import Unauthorized


def _ct_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def verify_admin_key(presented: str | None, expected: str) -> None:
    """Raise `Unauthorized` unless `presented` matches the admin key."""
    if not expected:
        raise Unauthorized(reason="admin key not configured")
    if not presented or not _ct_equal(presented, expected):
        raise Unauthorized(reason="invalid admin key")


def hash_stream_token(token: str) -> str:
    """One-way hash for at-rest storage of a per-subscription stream token."""
    return hashlib.sha256(token.encode()).hexdigest()


def verify_stream_token(presented: str | None, expected_hash: str) -> None:
    if not presented or not _ct_equal(hash_stream_token(presented), expected_hash):
        raise Unauthorized(reason="invalid stream token")


def extract_bearer(authorization: str | None, x_api_key: str | None) -> str | None:
    """Pull a key from either `Authorization: Bearer <k>` or `X-API-Key: <k>`."""
    if x_api_key:
        return x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None
