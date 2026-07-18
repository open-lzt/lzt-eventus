"""`EventEngine` — assembles the graph and supervises it (Law 13).

`build` wires the SQLAlchemy/Postgres stores for the real daemon; `run` takes the
single-owner lease, supervises sources + bus under one `TaskGroup`, and drains
cleanly on stop. Tests build an in-process engine via `eventus_fakes.build_fake_engine`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from pylzt.client import Client
from pylzt.config import ClientConfig

from lzt_eventus.account.reconciler import AccountReconciler, seed_lzt_tokens
from lzt_eventus.baseline.memory import MemoryLastSeenStore
from lzt_eventus.baseline.store import BaseLastSeenStore
from lzt_eventus.bus.catchup import CatchUpBus
from lzt_eventus.bus.dlq import BaseDeadLetterStore, MemoryDeadLetterStore
from lzt_eventus.config import EngineConfig
from lzt_eventus.consumers.consumer import BaseConsumer
from lzt_eventus.cursor.base import BaseCursorStore
from lzt_eventus.cursor.memory import MemoryCursorStore
from lzt_eventus.daemon.lease import BaseLease, NullLease
from lzt_eventus.dedup.seen import BaseSeenCache, MemorySeenCache, SeenCache
from lzt_eventus.delivery.delivery import WebhookDelivery
from lzt_eventus.delivery.transport import HttpxWebhookTransport
from lzt_eventus.errors import ConsumerNotFound
from lzt_eventus.lib.clock import Clock, RealClock
from lzt_eventus.log.base import BaseEventLog
from lzt_eventus.log.memory import MemoryEventLog
from lzt_eventus.sources.base import BaseSource
from lzt_eventus.sources.category import CategorySource
from lzt_eventus.sources.confirm import ConfirmQueue
from lzt_eventus.sources.conversations import ConversationsSource
from lzt_eventus.sources.guarantee import GuaranteeSeeder, GuaranteeWatcher
from lzt_eventus.sources.manager import SourceManager
from lzt_eventus.sources.notifications import NotificationsSource
from lzt_eventus.sources.payments import PaymentsSource
from lzt_eventus.sources.rating import RatingSource
from lzt_eventus.sources.rotation import RotatingSource
from lzt_eventus.transport import LogTransport

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from lzt_eventus.account.repo import BaseTokenAccountRepo
    from secret_box import SecretBox

_log = structlog.get_logger("lzt_eventus.engine")


@dataclass(frozen=True, slots=True)
class Stores:
    log: BaseEventLog
    last_seen: BaseLastSeenStore
    cursor: BaseCursorStore
    dlq: BaseDeadLetterStore
    seen: BaseSeenCache


class EventEngine:
    def __init__(
        self,
        *,
        client: Client,
        stores: Stores,
        config: EngineConfig,
        consumers: Sequence[BaseConsumer],
        lease: BaseLease | None = None,
        clock: Clock | None = None,
        delivery: WebhookDelivery | None = None,
        extra_sources: Sequence[BaseSource] = (),
        rating_clients: Sequence[Client] | None = None,
        token_repo: BaseTokenAccountRepo | None = None,
        secret_box: SecretBox | None = None,
    ) -> None:
        self._client = client
        self._stores = stores
        self._config = config
        self._clock = clock or RealClock()
        self._lease = lease or NullLease()
        self._delivery = delivery
        self._stop = asyncio.Event()
        self._token_repo = token_repo
        self._secret_box = secret_box
        self._account_reconciler: AccountReconciler | None = None
        if token_repo is not None and secret_box is not None:
            self._account_reconciler = AccountReconciler(
                repo=token_repo,
                engine=self,
                secret_box=secret_box,
                min_cadence=config.min_cadence,
                max_cadence=config.max_cadence,
                cadence=config.rating_cadence,
            )
        self._bus = CatchUpBus(
            stores.log,
            stores.cursor,
            stores.dlq,
            max_handle_attempts=config.max_handle_attempts,
            backoff_base=config.catchup_backoff_base,
            backoff_max=config.catchup_backoff_max,
            max_concurrent_consumers=config.bus_max_concurrent_consumers or None,
        )
        self._transport = LogTransport(stores.log, on_committed=self._bus.notify)
        for consumer in consumers:
            self._bus.register(consumer)
        confirm_cap = max(1, int(config.general_per_min * config.confirm_budget_fraction))
        confirm = ConfirmQueue(client, per_cycle_cap=confirm_cap)
        built: list[BaseSource] = [
            CategorySource(
                client=client,
                category=category,
                transport=self._transport,
                last_seen=stores.last_seen,
                confirm=confirm,
                disappear_polls=config.disappear_polls,
                poll_pages=config.poll_pages,
                per_page=config.per_page,
                min_cadence=config.min_cadence,
                max_cadence=config.max_cadence,
                cadence=config.default_cadence,
                clock=self._clock,
            )
            for category in config.categories
        ]
        built.extend(self._build_event_sources(client, stores, config))
        if rating_clients:
            built.append(self._build_rating_source(rating_clients, stores, config))
        built.extend(extra_sources)
        self._source_manager = SourceManager(built)

    def _build_event_sources(
        self,
        client: Client,
        stores: Stores,
        config: EngineConfig,
    ) -> list[BaseSource]:
        """Payments/notifications/conversations/guarantee sources sharing one dedup cache.

        `GuaranteeWatcher` is dual-role: it also needs its watch-list seeded from
        `ItemPurchased` events, so its companion `GuaranteeSeeder` is registered on
        the bus here alongside the source (mirrors the `consumers` registration loop
        above — both consume the same `stores.last_seen` watch-list rows).
        """
        token_id = config.tokens[0].get_secret_value() if config.tokens else "default"
        payments = PaymentsSource(
            client=client,
            token_id=token_id,
            transport=self._transport,
            seen=stores.seen,
            cursor=stores.cursor,
            last_seen=stores.last_seen,
            min_cadence=config.min_cadence,
            max_cadence=config.max_cadence,
            cadence=config.payments_cadence,
            clock=self._clock,
        )
        notifications = NotificationsSource(
            client=client,
            transport=self._transport,
            last_seen=stores.last_seen,
            cursors=stores.cursor,
            seen=stores.seen,
            min_cadence=config.min_cadence,
            max_cadence=config.max_cadence,
            cadence=config.notif_cadence,
            clock=self._clock,
        )
        conversations = ConversationsSource(
            client=client,
            transport=self._transport,
            seen=stores.seen,
            cursor_store=stores.cursor,
            last_seen=stores.last_seen,
            min_cadence=config.min_cadence,
            max_cadence=config.max_cadence,
            cadence=config.conversations_cadence,
            clock=self._clock,
        )
        guarantee = GuaranteeWatcher(
            client=client,
            transport=self._transport,
            last_seen=stores.last_seen,
            min_cadence=config.min_cadence,
            max_cadence=config.max_cadence,
            cadence=config.guarantee_check_interval,
            clock=self._clock,
        )
        self._bus.register(GuaranteeSeeder(client=client, last_seen=stores.last_seen))
        return [payments, notifications, conversations, guarantee]

    def _build_rating_source(
        self,
        rating_clients: Sequence[Client],
        stores: Stores,
        config: EngineConfig,
    ) -> BaseSource:
        """One `RatingSource` per account; 2+ accounts get pooled behind one `RotatingSource`.

        Every account has its own Forum `user_id`, and `RatingSource` already keys its
        `BaseLastSeenStore` row by that `user_id` (`_store_key`, sources/rating.py) — so the
        accounts safely share `stores.last_seen` with no extra namespacing needed.
        """
        accounts = tuple(rating_clients)
        units = [
            RatingSource(
                client=account,
                transport=self._transport,
                last_seen=stores.last_seen,
                min_cadence=config.min_cadence,
                max_cadence=config.max_cadence,
                cadence=config.rating_cadence,
                clock=self._clock,
            )
            for account in accounts
        ]
        if len(units) == 1:
            return units[0]
        rotation = RotatingSource(
            units=units,
            accounts_per_tick=config.rating_accounts_per_tick,
            min_cadence=config.min_cadence,
            max_cadence=config.max_cadence,
            cadence=config.rating_cadence,
            clock=self._clock,
        )
        rotation.name = "rating-rotation"
        return rotation

    @property
    def bus(self) -> CatchUpBus:
        return self._bus

    @property
    def stores(self) -> Stores:
        return self._stores

    @property
    def delivery(self) -> WebhookDelivery | None:
        return self._delivery

    def request_stop(self) -> None:
        self._stop.set()
        self._source_manager.request_stop()

    # Sources (producers) and consumers (subscribers) can be added/dropped while the
    # engine runs. Infra singletons (client, stores, config, lease, clock) stay
    # construction-time — hot-swapping the log/cursor mid-pump corrupts cursors.
    # Source lifecycle (start/restart-with-backoff/drain) is owned by SourceManager.

    @property
    def account_reconciler(self) -> AccountReconciler | None:
        """`None` unless the engine was built with a `token_repo` + `secret_box` (real daemon)."""
        return self._account_reconciler

    @property
    def token_repo(self) -> BaseTokenAccountRepo | None:
        return self._token_repo

    @property
    def secret_box(self) -> SecretBox | None:
        return self._secret_box

    @property
    def source_names(self) -> tuple[str, ...]:
        return self._source_manager.source_names

    def add_source(self, source: BaseSource) -> None:
        """Add a source at runtime; the supervisor starts it if the engine is running."""
        self._source_manager.add_source(source)

    def remove_source(self, name: str) -> None:
        """Drop a source at runtime; its task is stopped gracefully if running."""
        self._source_manager.remove_source(name)

    @property
    def consumer_names(self) -> tuple[str, ...]:
        return self._bus.consumer_names()

    def add_module(self, consumer: BaseConsumer) -> None:
        """Register a subscriber at runtime; picked up on the next bus pump."""
        self._bus.register(consumer)

    def remove_module(self, name: str) -> None:
        """Drop a subscriber at runtime; its cursor is left committed for resume."""
        if not self._bus.unregister(name):
            raise ConsumerNotFound(name)

    async def drain_once(self) -> int:
        """One poll of every category + one bus pump (used by tests / --dry-run)."""
        emitted = 0
        for source in self._source_manager.sources:
            emitted += await source.poll_once()
        await self._bus.pump_once()
        if self._delivery is not None:
            await self._delivery.pump_once()
        return emitted

    async def _reconcile_loop(self) -> None:
        """Periodic safety sweep on top of the admin service's per-mutation `reconcile()`.

        Catches drift (e.g. a source add/remove that was missed by an in-process
        mutation). Does NOT re-probe accounts whose registration deferred upstream
        verification (`metadata["_verify"]=="deferred"`) — that flag is informational
        only; a genuinely dead deferred token surfaces the normal way, once its
        source starts failing.
        """
        assert self._account_reconciler is not None
        while not self._stop.is_set():
            try:
                await self._account_reconciler.reconcile()
            except Exception:
                _log.exception("account_reconcile_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._config.account_reconcile_cadence
                )

    async def run(self) -> None:
        await self._lease.acquire()
        try:
            if self._token_repo is not None and self._secret_box is not None:
                await seed_lzt_tokens(
                    self._config, self._token_repo, self._secret_box, clock=self._clock
                )
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._bus.run(self._stop))
                if self._delivery is not None:
                    tg.create_task(self._delivery.run(self._stop))
                if self._account_reconciler is not None:
                    tg.create_task(self._reconcile_loop())
                tg.create_task(self._source_manager.supervise())
        finally:
            await self._lease.release()
            await self._client.aclose()

    @classmethod
    def build(
        cls,
        config: EngineConfig,
        *,
        consumers: Sequence[BaseConsumer],
        clock: Clock | None = None,
        extra_sources: Sequence[BaseSource] = (),
    ) -> tuple[EventEngine, async_sessionmaker[AsyncSession]]:
        """Wire the SQLAlchemy/Postgres stores for the real daemon.

        Returns the engine plus the shared `async_sessionmaker` so the daemon can
        build the web layer's repos over the *same* engine (Law 2). Postgres impls
        are imported lazily so `import lzt_eventus` stays I/O-free.
        """
        from lzt_eventus.baseline.store import BaselineStore
        from lzt_eventus.bus.dlq import DeadLetterStore
        from lzt_eventus.cursor.store import CursorStore
        from lzt_eventus.daemon.lease import PgAdvisoryLease
        from lzt_eventus.log.store import EventStore
        from lzt_eventus.orm.base import build_async_sessionmaker
        from lzt_eventus.web.repos.subscription_repo import PostgresSubscriptionRepo
        from lzt_eventus.web.repos.token_account_repo import PostgresTokenAccountRepo
        from secret_box import SecretBox

        sessionmaker = build_async_sessionmaker(config.database_url)
        # Fails loud (RuntimeError, plan Decision 2) if LZT_TOKEN_ENC_KEY is unset —
        # a running daemon must never fall back to storing credentials in plaintext.
        secret_box = SecretBox(config.token_enc_key.get_secret_value())
        token_repo = PostgresTokenAccountRepo(sessionmaker)
        last_seen = BaselineStore(sessionmaker)
        stores = Stores(
            log=EventStore(sessionmaker, last_seen),
            last_seen=last_seen,
            cursor=CursorStore(sessionmaker),
            dlq=DeadLetterStore(sessionmaker),
            seen=SeenCache.connect(config.redis_url, config.seen_ttl_seconds),
        )
        tokens = [token.get_secret_value() for token in config.tokens]
        client = (
            Client(tokens)
            if config.lzt_api_base_url is None
            else Client(tokens, config=ClientConfig(base_url=config.lzt_api_base_url))
        )
        engine_bind = sessionmaker.kw.get("bind")
        lease = PgAdvisoryLease(engine_bind, config.advisory_lock_key)
        delivery = WebhookDelivery(
            repo=PostgresSubscriptionRepo(sessionmaker),
            log=stores.log,
            cursors=stores.cursor,
            dlq=stores.dlq,
            transport=HttpxWebhookTransport(timeout=config.webhook_timeout),
            config=config,
        )
        engine = cls(
            client=client,
            stores=stores,
            config=config,
            consumers=consumers,
            lease=lease,
            clock=clock,
            delivery=delivery,
            extra_sources=extra_sources,
            token_repo=token_repo,
            secret_box=secret_box,
        )
        return engine, sessionmaker

    @classmethod
    def build_memory(
        cls,
        *,
        client: Client,
        config: EngineConfig | None = None,
        consumers: Sequence[BaseConsumer] = (),
        clock: Clock | None = None,
        extra_sources: Sequence[BaseSource] = (),
        rating_clients: Sequence[Client] | None = None,
    ) -> EventEngine:
        """Embedded, zero-infra EventEngine — in-memory stores, no Postgres/webhook transport."""
        last_seen = MemoryLastSeenStore()
        stores = Stores(
            log=MemoryEventLog(last_seen),
            last_seen=last_seen,
            cursor=MemoryCursorStore(),
            dlq=MemoryDeadLetterStore(),
            seen=MemorySeenCache(),
        )
        return cls(
            client=client,
            stores=stores,
            config=config or EngineConfig(),
            consumers=consumers,
            clock=clock,
            extra_sources=extra_sources,
            rating_clients=rating_clients,
        )
