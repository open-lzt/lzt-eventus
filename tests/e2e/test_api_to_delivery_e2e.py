"""E2E across the seam: management API creates a webhook subscription, the engine
delivers a real event to it, and the body verifies under the secret the API handed
back once. API, durable log, dispatcher and sink all share ONE set of Memory stores
— exactly the wiring `EventEngine.build` + `EngineHandle` produce in production.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pylzt.models.lot import Lot, LotFilter
from pylzt.pagination import Page, Paginator
from pylzt.types import Category, Currency, ItemId, ItemOrigin, SellerId
from pydantic import SecretStr

from eventus_fakes import build_fake_engine, fake_engine_handle
from lzt_eventus.config import EngineConfig
from lzt_eventus.delivery.signing import SIGNATURE_HEADER, verify_webhook
from lzt_eventus.delivery.transport import RecordingWebhookTransport
from lzt_eventus.web.main import build_app
from lzt_eventus.web.repos.subscription_repo import MemorySubscriptionRepo
from lzt_eventus.web.shared.handle import EngineHandle

pytestmark = pytest.mark.e2e

ADMIN = "admin-secret-key"


def mk_lot(item_id: int, price: str) -> Lot:
    return Lot(
        item_id=ItemId(item_id),
        category=Category.STEAM,
        price=Decimal(price),
        currency=Currency.RUB,
        title="acc",
        seller_id=SellerId(1),
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        item_state="active",
        item_origin=ItemOrigin.BRUTE,
        guarantee="",
        nsb=True,
        content_hash=f"{price}",
        attributes={},
    )


class FakeClient:
    def __init__(self) -> None:
        self.lots: list[Lot] = []
        self.market = self

    def list_lots(self, filter: LotFilter, *, max_pages: int | None = None) -> Paginator[Lot]:
        async def fetch(page: int) -> Page[Lot]:
            return Page(items=list(self.lots), has_more=False)

        return Paginator(fetch)

    async def get_lots_batch(self, item_ids: list[ItemId]) -> list[Lot]:
        return []

    async def execute(self, method: object) -> Page[object]:
        """Duck-typed stub for the event-source sources (payments/notif/conv): always empty."""
        return Page(items=[], has_more=False)

    async def aclose(self) -> None:
        return None


def _config() -> EngineConfig:
    return EngineConfig(
        admin_api_key=SecretStr(ADMIN),
        categories=[Category.STEAM],
        disappear_polls=1,
        poll_pages=1,
        per_page=50,
        default_cadence=1.0,
        tokens=["x"],
        webhook_backoff_base=0.0,
        webhook_backoff_max=0.0,
    )


class Wiring:
    """One set of Memory stores shared by the engine and the management API."""

    def __init__(self) -> None:
        self.client = FakeClient()
        self.repo = MemorySubscriptionRepo()
        self.transport = RecordingWebhookTransport()
        self.config = _config()
        self.engine = build_fake_engine(
            self.config,
            client=self.client,  # type: ignore[arg-type]
            consumers=[],
            subscriptions=self.repo,
            webhook_transport=self.transport,
        )

        async def _ready() -> bool:
            return True

        # This e2e wiring predates token accounts and has no need for one — reuse
        # `EngineHandle.memory()`'s token-account bootstrap rather than duplicating it.
        token_handle = fake_engine_handle(self.config)
        handle = EngineHandle(
            config=self.config,
            subscriptions=self.repo,  # same repo the engine's dispatcher reads
            event_log=self.engine.stores.log,
            cursors=self.engine.stores.cursor,
            ready=_ready,
            token_accounts=token_handle.token_accounts,
            secret_box=token_handle.secret_box,
            account_reconciler=token_handle.account_reconciler,
        )
        self.api = TestClient(build_app(handle))

    def create_webhook(self, *, backfill: bool = False) -> str:
        r = self.api.post(
            "/subscriptions/create",
            json={
                "transport": "webhook",
                "endpoint": "https://example.com/webhook",
                "event_types": ["new_lot"],
                "backfill": backfill,
            },
            headers={"X-API-Key": ADMIN},
        )
        assert r.status_code == 200
        secret = r.json()["data"]["secret"]
        assert isinstance(secret, str)
        return secret


async def test_api_created_subscription_receives_signed_event() -> None:
    w = Wiring()
    client, engine, api, transport = w.client, w.engine, w.api, w.transport
    client.lots = [mk_lot(1, "100")]

    created = api.post(
        "/subscriptions/create",
        json={
            "transport": "webhook",
            "endpoint": "https://example.com/webhook",
            "event_types": ["new_lot"],
        },
        headers={"X-API-Key": ADMIN},
    )
    assert created.status_code == 200
    secret = created.json()["data"]["secret"]
    assert secret  # one-time plaintext webhook HMAC key

    await engine.drain_once()  # bootstrap (snapshot only)
    assert transport.calls == []

    client.lots = [mk_lot(1, "100"), mk_lot(2, "50")]  # lot 2 → NEW_LOT
    await engine.drain_once()

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert verify_webhook(secret, call.body, call.headers[SIGNATURE_HEADER])
    assert b"new_lot" in call.body


async def test_new_subscription_does_not_replay_backlog() -> None:
    w = Wiring()
    # Build a backlog of NEW_LOT events BEFORE anyone subscribes.
    w.client.lots = [mk_lot(1, "100")]
    await w.engine.drain_once()  # bootstrap snapshot
    w.client.lots = [mk_lot(1, "100"), mk_lot(2, "50")]
    await w.engine.drain_once()  # NEW_LOT for lot 2 lands in the log
    assert w.transport.calls == []  # no subscription yet

    w.create_webhook()  # cursor seeded to the log head — backlog is skipped

    w.client.lots = [mk_lot(1, "100"), mk_lot(2, "50"), mk_lot(3, "30")]
    await w.engine.drain_once()  # only the new lot 3 is delivered

    assert len(w.transport.calls) == 1  # the pre-subscription backlog was NOT replayed


async def test_backfill_subscription_replays_backlog() -> None:
    w = Wiring()
    w.client.lots = [mk_lot(1, "100")]
    await w.engine.drain_once()  # bootstrap snapshot
    w.client.lots = [mk_lot(1, "100"), mk_lot(2, "50")]
    await w.engine.drain_once()  # NEW_LOT for lot 2 (backlog)
    assert w.transport.calls == []

    w.create_webhook(backfill=True)  # cursor stays at 0 — full replay

    w.client.lots = [mk_lot(1, "100"), mk_lot(2, "50"), mk_lot(3, "30")]
    await w.engine.drain_once()

    # both the historical lot-2 event and the new lot-3 event are delivered
    assert len(w.transport.calls) == 2
