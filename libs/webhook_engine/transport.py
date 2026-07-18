"""Webhook HTTP transport behind an ABC (Law 10) — httpx never leaks (Law 18).

`post` returns a `WebhookResponse`; the sink decides retry/park/pace from it. The
httpx backend imports lazily (Law 23) so `import webhook_engine` stays I/O- and
dep-free. `RecordingWebhookTransport` is the in-memory test double (Law 11): it
records every call and can inject N transport failures to exercise the retry/DLQ
paths.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from webhook_engine.errors import WebhookTransportError

if TYPE_CHECKING:
    import httpx


@dataclass(frozen=True, slots=True)
class WebhookResponse:
    status: int
    # `None` means the body carried no opinion (empty/malformed/no "ok" key) —
    # treated as success for backward compat with the old status-only contract.
    ok: bool | None
    retry_after: float | None


def _parse_receiver_response(status: int, body: bytes) -> WebhookResponse:
    """Best-effort JSON parse of a 2xx body for the receiver-interactive protocol.

    Any parse failure (empty body, malformed JSON, non-dict, missing "ok") is
    swallowed into `ok=None` — the receiver never gets to break delivery just by
    replying with an unexpected shape.
    """
    if not (200 <= status < 300):
        return WebhookResponse(status=status, ok=None, retry_after=None)
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return WebhookResponse(status=status, ok=None, retry_after=None)
    if not isinstance(parsed, dict) or "ok" not in parsed:
        return WebhookResponse(status=status, ok=None, retry_after=None)
    ok = bool(parsed["ok"])
    retry_after: float | None = None
    if ok is False and parsed.get("retry_after") is not None:
        try:
            retry_after = float(parsed["retry_after"])
        except (TypeError, ValueError):
            retry_after = None
    return WebhookResponse(status=status, ok=ok, retry_after=retry_after)


class BaseWebhookTransport(ABC):
    @abstractmethod
    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> WebhookResponse:
        """POST `body`; return the parsed response. Raise `WebhookTransportError` on no reply."""

    async def aclose(self) -> None:
        """Release any pooled connection. Idempotent; default no-op."""


class HttpxWebhookTransport(BaseWebhookTransport):
    def __init__(self, *, timeout: float = 10.0, max_response_bytes: int = 65536) -> None:
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        self._client: httpx.AsyncClient | None = None

    def _ensure(self) -> httpx.AsyncClient:
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> WebhookResponse:
        import httpx

        client = self._ensure()
        too_large = False
        try:
            async with client.stream("POST", url, content=body, headers=dict(headers)) as resp:
                buffer = bytearray()
                async for chunk in resp.aiter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > self._max_response_bytes:
                        # Break (not `raise` here) so the `async with` still closes the
                        # stream cleanly instead of leaking the connection.
                        too_large = True
                        break
                status = resp.status_code
        except httpx.HTTPError as exc:  # connection / timeout / DNS — retryable
            raise WebhookTransportError(url=url, reason=repr(exc)) from exc
        if too_large:
            raise WebhookTransportError(url=url, reason="response_too_large")
        return _parse_receiver_response(status, bytes(buffer))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


@dataclass(slots=True)
class RecordedCall:
    url: str
    body: bytes
    headers: dict[str, str]


class RecordingWebhookTransport(BaseWebhookTransport):
    """Test double: records deliveries, optionally fails the first `fail_times` POSTs."""

    def __init__(self, *, status: int = 200, fail_times: int = 0) -> None:
        self.calls: list[RecordedCall] = []
        self._status = status
        self._fail_times = fail_times

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> WebhookResponse:
        self.calls.append(RecordedCall(url=url, body=bytes(body), headers=dict(headers)))
        if self._fail_times > 0:
            self._fail_times -= 1
            raise WebhookTransportError(url=url, reason="injected failure")
        return WebhookResponse(status=self._status, ok=None, retry_after=None)
