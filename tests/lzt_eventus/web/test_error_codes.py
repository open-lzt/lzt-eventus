"""`ErrorCode` values are a wire contract with `lzt-eventus-sdk` — must stay byte-identical."""

from __future__ import annotations

from lzt_eventus.web.base import errors
from lzt_eventus.web.base.error_codes import ErrorCode
from lzt_eventus.web.routes.events import NotAPollingSubscription


def test_error_code_values_are_byte_identical() -> None:
    expected = {
        "ERROR": "error",
        "BAD_REQUEST": "bad_request",
        "UNAUTHORIZED": "unauthorized",
        "FORBIDDEN": "forbidden",
        "NOT_FOUND": "not_found",
        "CONFLICT": "conflict",
        "SERVICE_UNAVAILABLE": "service_unavailable",
        "UNKNOWN_EVENT_TYPE": "unknown_event_type",
        "INVALID_LIMIT": "invalid_limit",
        "LIMIT_TOO_LARGE": "limit_too_large",
        "UNSAFE_WEBHOOK_ENDPOINT": "unsafe_webhook_endpoint",
        "WEBHOOK_HANDSHAKE_FAILED": "webhook_handshake_failed",
        "TOKEN_INVALID_UPSTREAM": "token_invalid_upstream",
        "TOKEN_ACCOUNT_CAP_EXCEEDED": "token_account_cap_exceeded",
        "SIGNATURE_INVALID": "signature_invalid",
        "SUBSCRIPTION_NOT_FOUND": "subscription_not_found",
        "ALIAS_NOT_FOUND": "alias_not_found",
        "TOKEN_ACCOUNT_NOT_FOUND": "token_account_not_found",
        "ALIAS_ALREADY_EXISTS": "alias_already_exists",
        "NOT_A_POLLING_SUBSCRIPTION": "not_a_polling_subscription",
        "SUBSCRIPTION_CTX_MISMATCH": "subscription_ctx_mismatch",
        "SUBSCRIPTION_SCOPE_MISMATCH": "subscription_scope_mismatch",
    }
    assert {member.name: member.value for member in ErrorCode} == expected


def test_web_error_subclass_codes_match_enum() -> None:
    expected = {
        errors.WebError: ErrorCode.ERROR,
        errors.BadRequest: ErrorCode.BAD_REQUEST,
        errors.Unauthorized: ErrorCode.UNAUTHORIZED,
        errors.Forbidden: ErrorCode.FORBIDDEN,
        errors.NotFound: ErrorCode.NOT_FOUND,
        errors.Conflict: ErrorCode.CONFLICT,
        errors.ServiceUnavailable: ErrorCode.SERVICE_UNAVAILABLE,
        errors.UnknownEventType: ErrorCode.UNKNOWN_EVENT_TYPE,
        errors.InvalidLimit: ErrorCode.INVALID_LIMIT,
        errors.LimitTooLarge: ErrorCode.LIMIT_TOO_LARGE,
        errors.SubscriptionNotFound: ErrorCode.SUBSCRIPTION_NOT_FOUND,
        errors.UnsafeWebhookEndpoint: ErrorCode.UNSAFE_WEBHOOK_ENDPOINT,
        errors.WebhookHandshakeFailed: ErrorCode.WEBHOOK_HANDSHAKE_FAILED,
        errors.WebhookSignatureInvalid: ErrorCode.SIGNATURE_INVALID,
        errors.AliasAlreadyExists: ErrorCode.ALIAS_ALREADY_EXISTS,
        errors.AliasNotFound: ErrorCode.ALIAS_NOT_FOUND,
        errors.TokenAccountNotFound: ErrorCode.TOKEN_ACCOUNT_NOT_FOUND,
        errors.TokenInvalidUpstream: ErrorCode.TOKEN_INVALID_UPSTREAM,
        errors.TokenAccountCapExceeded: ErrorCode.TOKEN_ACCOUNT_CAP_EXCEEDED,
        NotAPollingSubscription: ErrorCode.NOT_A_POLLING_SUBSCRIPTION,
        errors.SubscriptionCtxMismatch: ErrorCode.SUBSCRIPTION_CTX_MISMATCH,
        errors.SubscriptionScopeMismatch: ErrorCode.SUBSCRIPTION_SCOPE_MISMATCH,
    }
    for cls, code in expected.items():
        assert cls.code == code, f"{cls.__name__}.code == {cls.code!r}, expected {code!r}"
        assert isinstance(cls.code, str)
