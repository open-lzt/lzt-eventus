"""E2E: poll → diff → durable log → delivery bus → signed webhook POST.

Drives the whole engine on Memory stores with a fake catalog and a recording
transport — no network, no Postgres — and asserts the contract end consumers rely
on: the body is HMAC-signed with the subscription secret, the event id doubles as
the idempotency key, a failing endpoint exhausts retries and parks in the DLQ
(cursor still advances), and runtime create/deactivate flips delivery on and off.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pylzt.models.lot import Lot, LotFilter
from pylzt.pagination import Page, Paginator
from pylzt.types import Category, Currency, ItemId, ItemOrigin, SellerId

from eventus_fakes import build_fake_engine
from lzt_eventus.config import EngineConfig
from lzt_eventus.delivery.signing import (
    EVENT_ID_HEADER,
    IDEMPOTENCY_HEADER,
    SIGNATURE_HEADER,
    verify_webhook,
)
from lzt_eventus.delivery.subscription import (
    Subscription,
    SubscriptionId,
    TransportKind,
)
from lzt_eventus.delivery.subscription_ctx import WebhookCtx
from lzt_eventus.delivery.transport import RecordingWebhookTransport
from lzt_eventus.engine import EventEngine
from lzt_eventus.events.base import EventType
from lzt_eventus.web.repos.subscription_repo import MemorySubscriptionRepo

pytestmark = pytest.mark.e2e

SECRET = "whsec_test_secret_value"


@pytest.fixture(autouse=True)
def _no_dns_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """This suite drives delivery/retry/signing behavior against reserved `.test`
    hostnames that never resolve — the SSRF re-check `WebhookSink.deliver` now runs
    before every attempt (see url_safety) would fail DNS resolution on them and mask
    the retry/DLQ assertions this file actually exercises. SSRF range coverage lives
    in tests/libs/webhook_engine/test_url_safety.py.
    """
    monkeypatch.setattr("webhook_engine.sink.assert_safe_webhook_url", lambda url: None)


def mk_lot(item_id: int, price: str, *, title: str = "acc") -> Lot:
    return Lot(
        item_id=ItemId(item_id),
        category=Category.STEAM,
        price=Decimal(price),
        currency=Currency.RUB,
        title=title,
        seller_id=SellerId(1),
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        item_state="active",
        item_origin=ItemOrigin.BRUTE,
        guarantee="",
        nsb=True,
        content_hash=f"{price}:{title}",
        attributes={},
    )


class FakeClient:
    def __init__(self) -> None:
        self.lots: list[Lot] = []
        self.batch: dict[ItemId, Lot] = {}
        self.market = self

    def list_lots(self, filter: LotFilter, *, max_pages: int | None = None) -> Paginator[Lot]:
        async def fetch(page: int) -> Page[Lot]:
            return Page(items=list(self.lots), has_more=False)

        return Paginator(fetch)

    async def get_lots_batch(self, item_ids: list[ItemId]) -> list[Lot]:
        return [self.batch[i] for i in item_ids if i in self.batch]

    async def execute(self, method: object) -> Page[object]:
        """Duck-typed stub for the event-source sources (payments/notif/conv): always empty."""
        return Page(items=[], has_more=False)

    async def aclose(self) -> None:
        return None


def _config(**kw: object) -> EngineConfig:
    base: dict[str, object] = {
        "categories": [Category.STEAM],
        "disappear_polls": 1,
        "poll_pages": 1,
        "per_page": 50,
        "default_cadence": 1.0,
        "tokens": ["x"],
        # zero backoff keeps the retry path instant under test
        "webhook_backoff_base": 0.0,
        "webhook_backoff_max": 0.0,
    }
    base.update(kw)
    return EngineConfig(**base)  # type: ignore[arg-type]


def _webhook_sub(*, endpoint: str, secret: str | None) -> Subscription:
    sub = Subscription(
        subscription_id=SubscriptionId("sub-e2e-1"),
        transport=TransportKind.WEBHOOK,
        endpoint=endpoint,
        event_types=frozenset({EventType.NEW_LOT}),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ctx=WebhookCtx(),
        secret=secret,
    )
    return sub


def _engine(
    client: FakeClient,
    repo: MemorySubscriptionRepo,
    transport: RecordingWebhookTransport,
    **cfg: object,
) -> EventEngine:
    return build_fake_engine(
        _config(**cfg),
        client=client,  # type: ignore[arg-type]
        consumers=[],
        subscriptions=repo,
        webhook_transport=transport,
    )


async def test_new_lot_is_delivered_signed_with_subscription_secret() -> None:
    client = FakeClient()
    client.lots = [mk_lot(1, "100")]
    repo = MemorySubscriptionRepo()
    await repo.add(_webhook_sub(endpoint="https://hook.test/in", secret=SECRET))
    transport = RecordingWebhookTransport()
    engine = _engine(client, repo, transport)

    await engine.drain_once()  # bootstrap: snapshot only, no NewLot
    assert transport.calls == []

    client.lots = [mk_lot(1, "100"), mk_lot(2, "50")]  # lot 2 is new
    await engine.drain_once()

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call.url == "https://hook.test/in"
    # body is signed with the subscription's secret — receiver can verify
    assert verify_webhook(SECRET, call.body, call.headers[SIGNATURE_HEADER])
    # event id == idempotency key (at-least-once dedup on the receiver)
    assert call.headers[EVENT_ID_HEADER] == call.headers[IDEMPOTENCY_HEADER]
    assert b"new_lot" in call.body


async def test_only_subscribed_event_types_are_delivered() -> None:
    client = FakeClient()
    client.lots = [mk_lot(1, "100")]
    repo = MemorySubscriptionRepo()
    await repo.add(_webhook_sub(endpoint="https://hook.test/in", secret=SECRET))
    transport = RecordingWebhookTransport()
    engine = _engine(client, repo, transport)

    await engine.drain_once()  # bootstrap
    client.lots = [mk_lot(1, "80")]  # PRICE_DROPPED, NOT new_lot
    await engine.drain_once()

    # subscription only wants new_lot → price drop is filtered out
    assert transport.calls == []


async def test_failing_endpoint_exhausts_retries_and_parks_in_dlq() -> None:
    client = FakeClient()
    client.lots = [mk_lot(1, "100")]
    repo = MemorySubscriptionRepo()
    sub = _webhook_sub(endpoint="https://down.test/in", secret=SECRET)
    await repo.add(sub)
    # every POST raises → sink exhausts webhook_max_attempts → bus parks once
    transport = RecordingWebhookTransport(fail_times=1000)
    engine = _engine(client, repo, transport, webhook_max_attempts=3)

    await engine.drain_once()  # bootstrap
    client.lots = [mk_lot(1, "100"), mk_lot(2, "50")]
    await engine.drain_once()

    assert len(transport.calls) == 3  # tried exactly webhook_max_attempts times
    parked = await engine.stores.dlq.drain(sub.consumer_name())
    assert len(parked) == 1
    assert parked[0].event.event_type == EventType.NEW_LOT


async def test_4xx_is_terminal_no_retry() -> None:
    client = FakeClient()
    client.lots = [mk_lot(1, "100")]
    repo = MemorySubscriptionRepo()
    await repo.add(_webhook_sub(endpoint="https://hook.test/in", secret=SECRET))
    transport = RecordingWebhookTransport(status=400)
    engine = _engine(client, repo, transport, webhook_max_attempts=5)

    await engine.drain_once()
    client.lots = [mk_lot(1, "100"), mk_lot(2, "50")]
    await engine.drain_once()

    assert len(transport.calls) == 1  # 400 is a permanent client error → no retry


async def test_runtime_create_then_deactivate_toggles_delivery() -> None:
    client = FakeClient()
    client.lots = [mk_lot(1, "100")]
    repo = MemorySubscriptionRepo()
    transport = RecordingWebhookTransport()
    engine = _engine(client, repo, transport)

    await engine.drain_once()  # bootstrap, no subscription yet

    # subscription created at runtime — dispatcher picks it up on the next pump
    sub = _webhook_sub(endpoint="https://hook.test/in", secret=SECRET)
    await repo.add(sub)
    client.lots = [mk_lot(1, "100"), mk_lot(2, "50")]
    await engine.drain_once()
    assert len(transport.calls) == 1

    # deactivate → no further deliveries even as new events arrive
    await repo.replace(replace(sub, active=False))
    client.lots = [mk_lot(1, "100"), mk_lot(2, "50"), mk_lot(3, "30")]
    await engine.drain_once()
    assert len(transport.calls) == 1  # unchanged
