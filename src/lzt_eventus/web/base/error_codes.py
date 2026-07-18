"""Wire-stable error codes for `web.base.errors.WebError` and its subclasses.

Values are byte-identical to the strings shipped before this enum existed —
`lzt-eventus-sdk` depends on them (see AGENTS.md cross-repo rule).
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    ERROR = "error"
    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    SERVICE_UNAVAILABLE = "service_unavailable"
    UNKNOWN_EVENT_TYPE = "unknown_event_type"
    INVALID_LIMIT = "invalid_limit"
    LIMIT_TOO_LARGE = "limit_too_large"
    UNSAFE_WEBHOOK_ENDPOINT = "unsafe_webhook_endpoint"
    WEBHOOK_HANDSHAKE_FAILED = "webhook_handshake_failed"
    TOKEN_INVALID_UPSTREAM = "token_invalid_upstream"
    TOKEN_ACCOUNT_CAP_EXCEEDED = "token_account_cap_exceeded"
    SIGNATURE_INVALID = "signature_invalid"
    SUBSCRIPTION_NOT_FOUND = "subscription_not_found"
    ALIAS_NOT_FOUND = "alias_not_found"
    TOKEN_ACCOUNT_NOT_FOUND = "token_account_not_found"
    ALIAS_ALREADY_EXISTS = "alias_already_exists"
    NOT_A_POLLING_SUBSCRIPTION = "not_a_polling_subscription"
    SUBSCRIPTION_CTX_MISMATCH = "subscription_ctx_mismatch"
    SUBSCRIPTION_SCOPE_MISMATCH = "subscription_scope_mismatch"
