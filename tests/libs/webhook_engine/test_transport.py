from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from webhook_engine.errors import WebhookTransportError
from webhook_engine.transport import HttpxWebhookTransport


class _CountingByteStream(httpx.AsyncByteStream):
    """Yields `chunk` `n_chunks` times; `consumed` proves how many were actually pulled."""

    def __init__(self, chunk: bytes, n_chunks: int) -> None:
        self._chunk = chunk
        self._n_chunks = n_chunks
        self.consumed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self._n_chunks):
            self.consumed += 1
            yield self._chunk

    async def aclose(self) -> None:
        return None


def _transport_returning(stream: httpx.AsyncByteStream, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, stream=stream)

    return httpx.MockTransport(handler)


def _wire(webhook_transport: HttpxWebhookTransport, mock_transport: httpx.MockTransport) -> None:
    # `HttpxWebhookTransport._ensure()` lazily builds its own `httpx.AsyncClient`; for a unit
    # test we need to swap in one bound to `MockTransport` instead of touching the network.
    webhook_transport._client = httpx.AsyncClient(transport=mock_transport)


async def test_oversized_response_aborts_read_and_raises() -> None:
    chunk = b"x" * 20_000
    stream = _CountingByteStream(chunk, n_chunks=10)  # 200 KiB total if fully drained
    webhook_transport = HttpxWebhookTransport(max_response_bytes=50_000)
    _wire(webhook_transport, _transport_returning(stream))

    with pytest.raises(WebhookTransportError) as exc_info:
        await webhook_transport.post("https://example.com/hook", b"{}", {})

    assert exc_info.value.reason == "response_too_large"
    # 3 chunks (60 KiB) already trips the 50 KiB cap — the loop must not have drained
    # all 10 chunks (200 KiB) before aborting.
    assert stream.consumed < 10


async def test_small_response_under_cap_returns_status() -> None:
    stream = _CountingByteStream(b"ok", n_chunks=1)
    webhook_transport = HttpxWebhookTransport(max_response_bytes=50_000)
    _wire(webhook_transport, _transport_returning(stream, status=204))

    response = await webhook_transport.post("https://example.com/hook", b"{}", {})

    assert response.status == 204


async def test_ok_true_body_parsed() -> None:
    stream = _CountingByteStream(b'{"ok": true}', n_chunks=1)
    webhook_transport = HttpxWebhookTransport(max_response_bytes=50_000)
    _wire(webhook_transport, _transport_returning(stream, status=200))

    response = await webhook_transport.post("https://example.com/hook", b"{}", {})

    assert response.status == 200
    assert response.ok is True
    assert response.retry_after is None


async def test_ok_false_with_retry_after_parsed() -> None:
    stream = _CountingByteStream(b'{"ok": false, "retry_after": 5}', n_chunks=1)
    webhook_transport = HttpxWebhookTransport(max_response_bytes=50_000)
    _wire(webhook_transport, _transport_returning(stream, status=200))

    response = await webhook_transport.post("https://example.com/hook", b"{}", {})

    assert response.status == 200
    assert response.ok is False
    assert response.retry_after == 5.0


async def test_malformed_body_on_2xx_treated_as_success() -> None:
    """Empty/malformed/non-dict/no-"ok"-key bodies must not break the old
    status-only contract — `ok=None` is the "no opinion" signal the sink treats
    as success.
    """
    stream = _CountingByteStream(b"not json at all", n_chunks=1)
    webhook_transport = HttpxWebhookTransport(max_response_bytes=50_000)
    _wire(webhook_transport, _transport_returning(stream, status=200))

    response = await webhook_transport.post("https://example.com/hook", b"{}", {})

    assert response.status == 200
    assert response.ok is None
    assert response.retry_after is None


async def test_non_2xx_status_never_parses_body() -> None:
    stream = _CountingByteStream(b'{"ok": false, "retry_after": 5}', n_chunks=1)
    webhook_transport = HttpxWebhookTransport(max_response_bytes=50_000)
    _wire(webhook_transport, _transport_returning(stream, status=500))

    response = await webhook_transport.post("https://example.com/hook", b"{}", {})

    assert response.status == 500
    assert response.ok is None
    assert response.retry_after is None
