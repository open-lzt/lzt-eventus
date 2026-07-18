from __future__ import annotations

from collections.abc import Mapping

import pytest

from webhook_engine.config import WebhookEngineConfig
from webhook_engine.errors import UnsafeWebhookUrl, WebhookDeliveryError
from webhook_engine.sink import WebhookSink
from webhook_engine.transport import BaseWebhookTransport, WebhookResponse


class _FakeTransport(BaseWebhookTransport):
    """Answers with a fixed (retryable) status — isolates the SSRF re-check from the
    rest of the transport/retry machinery. The retryable status is what forces a
    second attempt to happen at all; the rebinding check must trip before that
    second attempt reaches `post()`.
    """

    def __init__(
        self, status: int = 503, *, ok: bool | None = None, retry_after: float | None = None
    ) -> None:
        self._status = status
        self._ok = ok
        self._retry_after = retry_after
        self.post_calls = 0

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> WebhookResponse:
        self.post_calls += 1
        return WebhookResponse(status=self._status, ok=self._ok, retry_after=self._retry_after)

    async def aclose(self) -> None:
        return None


async def test_rebinding_mid_delivery_aborts_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding between the registration-time check and delivery must abort the
    attempt loop immediately — the same terminal path as an existing non-retryable 4xx,
    never a retry (retrying would just resubmit to the now-unsafe address).
    """
    transport = _FakeTransport(status=503)  # retryable, so the sink attempts a 2nd round
    sink = WebhookSink(
        sink_id="sink-1",
        endpoint="https://example.com/hook",
        secret="s3cr3t",
        transport=transport,
        config=WebhookEngineConfig(max_attempts=5, backoff_base=0.0, backoff_max=0.0),
    )

    calls = 0

    def _rebinds_on_second_check(url: str) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise UnsafeWebhookUrl(url=url, reason="resolved to blocked IP range: 169.254.169.254")

    monkeypatch.setattr("webhook_engine.sink.assert_safe_webhook_url", _rebinds_on_second_check)

    with pytest.raises(WebhookDeliveryError) as exc_info:
        await sink.deliver(event_id="evt-1", event_type="test.event", body=b"{}")

    assert "unsafe_url" in exc_info.value.reason
    # first attempt succeeds the pre-check; a second attempt is never spent posting —
    # the loop must break on the second (unsafe) check before another transport.post.
    assert calls == 2
    assert transport.post_calls == 1


async def test_safe_url_checked_before_every_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport(status=200)
    sink = WebhookSink(
        sink_id="sink-1",
        endpoint="https://example.com/hook",
        secret="s3cr3t",
        transport=transport,
        config=WebhookEngineConfig(max_attempts=3),
    )

    calls = 0

    def _always_safe(url: str) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr("webhook_engine.sink.assert_safe_webhook_url", _always_safe)

    await sink.deliver(event_id="evt-1", event_type="test.event", body=b"{}")

    assert calls == 1
    assert transport.post_calls == 1


class _SequencedTransport(BaseWebhookTransport):
    """Returns one `WebhookResponse` (or raises) per call, in order — for tests that
    need the receiver's answer to change between attempts (e.g. `ok=false` then
    `ok=true` on redelivery).
    """

    def __init__(self, responses: list[WebhookResponse]) -> None:
        self._responses = list(responses)
        self.post_calls = 0

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> WebhookResponse:
        self.post_calls += 1
        return self._responses[self.post_calls - 1]

    async def aclose(self) -> None:
        return None


async def test_ok_true_is_immediate_success(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport(status=200, ok=True)
    sink = WebhookSink(
        sink_id="sink-1",
        endpoint="https://example.com/hook",
        secret="s3cr3t",
        transport=transport,
        config=WebhookEngineConfig(max_attempts=3),
    )
    monkeypatch.setattr("webhook_engine.sink.assert_safe_webhook_url", lambda url: None)

    await sink.deliver(event_id="evt-1", event_type="test.event", body=b"{}")

    assert transport.post_calls == 1


async def test_ok_false_schedules_retry_after_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Receiver asks for a 100s redeliver but `retry_after_cap` is 5 — the sink must
    honor the receiver's pacing intent while never exceeding the configured ceiling,
    and it must not treat the `ok=false` reply as either success or a hard failure.
    """
    transport = _SequencedTransport(
        [
            WebhookResponse(status=200, ok=False, retry_after=100.0),
            WebhookResponse(status=200, ok=True, retry_after=None),
        ]
    )
    delays: list[float] = []

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    sink = WebhookSink(
        sink_id="sink-1",
        endpoint="https://example.com/hook",
        secret="s3cr3t",
        transport=transport,
        config=WebhookEngineConfig(
            max_attempts=3, backoff_base=1.0, backoff_max=30.0, retry_after_cap=5.0
        ),
        sleep=_sleep,
    )
    monkeypatch.setattr("webhook_engine.sink.assert_safe_webhook_url", lambda url: None)

    await sink.deliver(event_id="evt-1", event_type="test.event", body=b"{}")

    assert transport.post_calls == 2
    assert delays == [5.0]  # min(100, retry_after_cap=5) — not the exponential-backoff delay


async def test_ok_false_uses_backoff_base_when_no_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _SequencedTransport(
        [
            WebhookResponse(status=200, ok=False, retry_after=None),
            WebhookResponse(status=200, ok=True, retry_after=None),
        ]
    )
    delays: list[float] = []

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    sink = WebhookSink(
        sink_id="sink-1",
        endpoint="https://example.com/hook",
        secret="s3cr3t",
        transport=transport,
        config=WebhookEngineConfig(
            max_attempts=3, backoff_base=2.0, backoff_max=30.0, retry_after_cap=10.0
        ),
        sleep=_sleep,
    )
    monkeypatch.setattr("webhook_engine.sink.assert_safe_webhook_url", lambda url: None)

    await sink.deliver(event_id="evt-1", event_type="test.event", body=b"{}")

    assert delays == [2.0]  # falls back to backoff_base, still clamped by retry_after_cap


async def test_malformed_body_ok_none_treated_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport(status=200, ok=None)
    sink = WebhookSink(
        sink_id="sink-1",
        endpoint="https://example.com/hook",
        secret="s3cr3t",
        transport=transport,
        config=WebhookEngineConfig(max_attempts=3),
    )
    monkeypatch.setattr("webhook_engine.sink.assert_safe_webhook_url", lambda url: None)

    await sink.deliver(event_id="evt-1", event_type="test.event", body=b"{}")

    assert transport.post_calls == 1


async def test_timeout_path_unaffected_by_response_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transport timeout must keep using the plain exponential-backoff path —
    the receiver only gets pacing control when it actually answers, never by
    staying silent (raising instead of responding).
    """
    from webhook_engine.errors import WebhookTransportError

    class _TimingOutTransport(BaseWebhookTransport):
        def __init__(self) -> None:
            self.post_calls = 0

        async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> WebhookResponse:
            self.post_calls += 1
            raise WebhookTransportError(url=url, reason="timeout")

        async def aclose(self) -> None:
            return None

    transport = _TimingOutTransport()
    delays: list[float] = []

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    sink = WebhookSink(
        sink_id="sink-1",
        endpoint="https://example.com/hook",
        secret="s3cr3t",
        transport=transport,
        config=WebhookEngineConfig(
            max_attempts=3, backoff_base=1.0, backoff_max=30.0, retry_after_cap=5.0
        ),
        sleep=_sleep,
    )
    monkeypatch.setattr("webhook_engine.sink.assert_safe_webhook_url", lambda url: None)

    with pytest.raises(WebhookDeliveryError):
        await sink.deliver(event_id="evt-1", event_type="test.event", body=b"{}")

    assert transport.post_calls == 3
    assert delays == [1.0, 2.0]  # unchanged exponential curve, never retry_after_cap-clamped
