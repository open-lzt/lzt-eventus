"""`WebhookSink` — signs + POSTs one pre-encoded body, retrying with backoff.

Framework-agnostic: it takes plain metadata (`sink_id`/`endpoint`/`secret`) and an
already-encoded body — no coupling to any particular event or bus shape. The host
application is responsible for turning its own domain event into `(event_id,
event_type, body)` before calling `deliver`. Raises `WebhookDeliveryError` only when
every attempt is spent; the caller decides what "parking" means (DLQ, log, etc.).
Client (4xx) responses are terminal and skip the retry loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from webhook_engine.config import WebhookEngineConfig
from webhook_engine.errors import UnsafeWebhookUrl, WebhookDeliveryError, WebhookTransportError
from webhook_engine.signing import (
    EVENT_ID_HEADER,
    EVENT_TYPE_HEADER,
    IDEMPOTENCY_HEADER,
    SIGNATURE_HEADER,
    signature_header,
)
from webhook_engine.transport import BaseWebhookTransport
from webhook_engine.url_safety import assert_safe_webhook_url


def _retryable(status: int) -> bool:
    """5xx and the two transient 4xx (timeout / rate-limit) are worth retrying."""
    return status >= 500 or status in (408, 429)


class WebhookSink:
    def __init__(
        self,
        *,
        sink_id: str,
        endpoint: str,
        secret: str | None,
        transport: BaseWebhookTransport,
        config: WebhookEngineConfig,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.sink_id = sink_id
        self.endpoint = endpoint
        self._secret = secret
        self._transport = transport
        self._config = config
        self._sleep = sleep or asyncio.sleep

    def _headers(self, event_id: str, event_type: str, body: bytes) -> dict[str, str]:
        return {
            "content-type": "application/json",
            EVENT_ID_HEADER: event_id,
            EVENT_TYPE_HEADER: event_type,
            SIGNATURE_HEADER: signature_header(self._secret or "", body),
            IDEMPOTENCY_HEADER: event_id,
        }

    async def deliver(self, *, event_id: str, event_type: str, body: bytes) -> None:
        headers = self._headers(event_id, event_type, body)
        attempts = self._config.max_attempts
        delay = self._config.backoff_base
        reason = "no attempt made"
        for attempt in range(attempts):
            next_delay: float | None = None
            try:
                assert_safe_webhook_url(self.endpoint)
                response = await self._transport.post(self.endpoint, body, headers)
                if 200 <= response.status < 300:
                    if response.ok is False:
                        # Receiver replied 2xx but asked for a redeliver — this is
                        # pacing control, not a failure: it never counts as a retry
                        # exhaustion candidate reason, and it uses the receiver's
                        # requested delay instead of the exponential backoff curve.
                        reason = "receiver_requested_retry"
                        next_delay = min(
                            response.retry_after or self._config.backoff_base,
                            self._config.retry_after_cap,
                        )
                    else:
                        return
                else:
                    reason = f"http_{response.status}"
                    if not _retryable(response.status):
                        break  # permanent client error — parking now beats hammering
            except UnsafeWebhookUrl as exc:
                # DNS rebinding since registration/last attempt — terminal, no retry.
                reason = f"unsafe_url:{exc.reason}"
                break
            except WebhookTransportError as exc:
                reason = exc.reason
            if attempt + 1 < attempts:
                if next_delay is not None:
                    await self._sleep(next_delay)
                else:
                    await self._sleep(min(delay, self._config.backoff_max))
                    delay *= 2
        raise WebhookDeliveryError(sink_id=self.sink_id, endpoint=self.endpoint, reason=reason)
