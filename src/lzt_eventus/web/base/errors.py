"""Web error tree — handlers `raise` these; the middleware maps them to HTTP.

`HTTPException` is banned (backend rule). A handler raises a typed `WebError`
carrying args, and `middlewares/errors.py` is the single place an error becomes an
HTTP response. Generic stand-ins (`BadRequest`/`Unauthorized`/…) are the only
sanctioned substitutes for `HTTPException`; domain errors subclass them.
"""

from __future__ import annotations

from typing import Any

from lzt_eventus.web.base.error_codes import ErrorCode


class WebError(Exception):
    status_code: int = 400
    code: ErrorCode = ErrorCode.ERROR

    def __init__(self, **fields: Any) -> None:
        self.fields = fields
        super().__init__(self.code)


class BadRequest(WebError):
    status_code = 400
    code = ErrorCode.BAD_REQUEST


class Unauthorized(WebError):
    status_code = 401
    code = ErrorCode.UNAUTHORIZED


class Forbidden(WebError):
    status_code = 403
    code = ErrorCode.FORBIDDEN


class NotFound(WebError):
    status_code = 404
    code = ErrorCode.NOT_FOUND


class Conflict(WebError):
    status_code = 409
    code = ErrorCode.CONFLICT


class ServiceUnavailable(WebError):
    status_code = 503
    code = ErrorCode.SERVICE_UNAVAILABLE


class UnknownEventType(BadRequest):
    code = ErrorCode.UNKNOWN_EVENT_TYPE


class InvalidLimit(BadRequest):
    code = ErrorCode.INVALID_LIMIT


class LimitTooLarge(BadRequest):
    code = ErrorCode.LIMIT_TOO_LARGE


class SubscriptionNotFound(NotFound):
    code = ErrorCode.SUBSCRIPTION_NOT_FOUND


class UnsafeWebhookEndpoint(BadRequest):
    code = ErrorCode.UNSAFE_WEBHOOK_ENDPOINT


class SubscriptionCtxMismatch(BadRequest):
    code = ErrorCode.SUBSCRIPTION_CTX_MISMATCH


class SubscriptionScopeMismatch(BadRequest):
    code = ErrorCode.SUBSCRIPTION_SCOPE_MISMATCH


class WebhookHandshakeFailed(BadRequest):
    code = ErrorCode.WEBHOOK_HANDSHAKE_FAILED


class WebhookSignatureInvalid(Unauthorized):
    code = ErrorCode.SIGNATURE_INVALID


class AliasAlreadyExists(Conflict):
    code = ErrorCode.ALIAS_ALREADY_EXISTS


class AliasNotFound(NotFound):
    code = ErrorCode.ALIAS_NOT_FOUND


class TokenAccountNotFound(NotFound):
    code = ErrorCode.TOKEN_ACCOUNT_NOT_FOUND


class TokenInvalidUpstream(BadRequest):
    code = ErrorCode.TOKEN_INVALID_UPSTREAM


class TokenAccountCapExceeded(BadRequest):
    code = ErrorCode.TOKEN_ACCOUNT_CAP_EXCEEDED
