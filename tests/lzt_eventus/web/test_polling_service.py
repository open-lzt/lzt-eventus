"""`PollingService.peek` — the empty-batch long-poll delay driven by `PollingCtx`."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from eventus_fakes import FakeCursorStore, FakeEventLog, FakeLastSeenStore
from lzt_eventus.delivery.subscription import Subscription, SubscriptionId, TransportKind
from lzt_eventus.delivery.subscription_ctx import PollingCtx
from lzt_eventus.events.base import EventType
from lzt_eventus.web.services.polling import PollingService


def _sub(*, poll_delay_seconds: float) -> Subscription:
    return Subscription(
        subscription_id=SubscriptionId("sub-poll-1"),
        transport=TransportKind.POLLING,
        endpoint="n/a",
        event_types=frozenset({EventType.NEW_LOT}),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ctx=PollingCtx(poll_delay_seconds=poll_delay_seconds),
    )


@pytest.mark.asyncio
async def test_empty_batch_waits_ctx_delay() -> None:
    svc = PollingService(FakeEventLog(FakeLastSeenStore()), FakeCursorStore())
    started = time.monotonic()
    batch = await svc.peek(_sub(poll_delay_seconds=0.1), None, limit=10)
    assert batch.items == []
    assert time.monotonic() - started >= 0.08  # asyncio.sleep granularity margin


@pytest.mark.asyncio
async def test_zero_delay_returns_immediately() -> None:
    svc = PollingService(FakeEventLog(FakeLastSeenStore()), FakeCursorStore())
    started = time.monotonic()
    await svc.peek(_sub(poll_delay_seconds=0.0), None, limit=10)
    assert time.monotonic() - started < 0.08
