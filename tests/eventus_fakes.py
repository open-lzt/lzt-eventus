"""In-process fake stores + engine builders for the test suite.

The store doubles are re-exports of the production `Memory*` implementations
(`EventEngine.build_memory`'s stores, Law 29) — a single source of truth, no
duplicated store logic between `src/` and `tests/`. Store *semantics* are pinned
against real Postgres by `test_stores_contract.py` (postgres-gated, runs in CI).

Imported flat as `eventus_fakes` (tests/ is on `pythonpath`, no package/__init__).
"""

from __future__ import annotations

from collections.abc import Sequence

from pylzt.client import Client

from lzt_eventus.baseline.memory import MemoryLastSeenStore as FakeLastSeenStore
from lzt_eventus.bus.dlq import MemoryDeadLetterStore as FakeDeadLetterStore
from lzt_eventus.config import EngineConfig
from lzt_eventus.consumers.consumer import BaseConsumer
from lzt_eventus.cursor.memory import MemoryCursorStore as FakeCursorStore
from lzt_eventus.dedup.seen import MemorySeenCache as FakeSeenCache
from lzt_eventus.delivery.delivery import WebhookDelivery
from lzt_eventus.delivery.transport import BaseWebhookTransport, HttpxWebhookTransport
from lzt_eventus.engine import EventEngine, Stores
from lzt_eventus.lib.clock import Clock
from lzt_eventus.log.memory import MemoryEventLog as FakeEventLog
from lzt_eventus.sources.base import BaseSource

_BOOT_TOKEN = "memory-boot-token"


def fake_stores() -> Stores:
    last_seen = FakeLastSeenStore()
    return Stores(
        log=FakeEventLog(last_seen),
        last_seen=last_seen,
        cursor=FakeCursorStore(),
        dlq=FakeDeadLetterStore(),
        seen=FakeSeenCache(),
    )


def build_fake_engine(
    config: EngineConfig,
    *,
    client: Client,
    consumers: Sequence[BaseConsumer],
    clock: Clock | None = None,
    subscriptions: object | None = None,
    webhook_transport: BaseWebhookTransport | None = None,
    extra_sources: Sequence[BaseSource] = (),
    rating_clients: Sequence[Client] | None = None,
) -> EventEngine:
    """In-process `EventEngine` over fake stores — the old `build_memory`, moved to tests."""
    stores = fake_stores()
    delivery: WebhookDelivery | None = None
    if subscriptions is not None:
        transport = webhook_transport or HttpxWebhookTransport(timeout=config.webhook_timeout)
        delivery = WebhookDelivery(
            repo=subscriptions,  # type: ignore[arg-type]
            log=stores.log,
            cursors=stores.cursor,
            dlq=stores.dlq,
            transport=transport,
            config=config,
        )
    return EventEngine(
        client=client,
        stores=stores,
        config=config,
        consumers=consumers,
        clock=clock,
        delivery=delivery,
        extra_sources=extra_sources,
        rating_clients=rating_clients,
    )


def fake_engine_handle(config: EngineConfig | None = None):
    """In-process `EngineHandle` for web API tests — the old `EngineHandle.memory()`."""
    from lzt_eventus.account.reconciler import AccountReconciler
    from lzt_eventus.web.repos.subscription_repo import MemorySubscriptionRepo
    from lzt_eventus.web.repos.token_account_repo import MemoryTokenAccountRepo
    from lzt_eventus.web.shared.handle import EngineHandle
    from secret_box import SecretBox

    cfg = config or EngineConfig()
    last_seen = FakeLastSeenStore()

    async def _ready() -> bool:
        return True

    token_accounts = MemoryTokenAccountRepo()
    enc_key = cfg.token_enc_key.get_secret_value() or "memory-only-test-key-not-for-prod"
    secret_box = SecretBox(enc_key)
    source_engine = build_fake_engine(cfg, client=Client([_BOOT_TOKEN]), consumers=[])
    reconciler = AccountReconciler(
        repo=token_accounts,
        engine=source_engine,
        secret_box=secret_box,
        min_cadence=cfg.min_cadence,
        max_cadence=cfg.max_cadence,
        cadence=cfg.rating_cadence,
    )
    return EngineHandle(
        config=cfg,
        subscriptions=MemorySubscriptionRepo(),
        event_log=FakeEventLog(last_seen),
        cursors=FakeCursorStore(),
        ready=_ready,
        token_accounts=token_accounts,
        secret_box=secret_box,
        account_reconciler=reconciler,
    )
