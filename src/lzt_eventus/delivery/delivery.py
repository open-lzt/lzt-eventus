"""`WebhookDelivery` — the facade that turns the durable log into signed POSTs.

It owns a dedicated `CatchUpBus` (its own cursor namespace via `sink:<id>` keys,
`max_handle_attempts=1` because the sink already retries internally) fed by the
`WebhookDispatcher`. Shares the engine's log / cursor / DLQ stores so a parked
delivery is redrivable with the same `redrive --consumer sink:<id>` ops hook.
"""

from __future__ import annotations

import asyncio

from pylzt.lib.metrics import BaseMetrics

from lzt_eventus.bus.catchup import CatchUpBus
from lzt_eventus.bus.dlq import BaseDeadLetterStore
from lzt_eventus.config import EngineConfig
from lzt_eventus.cursor.base import BaseCursorStore
from lzt_eventus.delivery.dispatcher import WebhookDispatcher
from lzt_eventus.delivery.repo import BaseSubscriptionRepo
from lzt_eventus.delivery.transport import BaseWebhookTransport
from lzt_eventus.log.base import BaseEventLog


class WebhookDelivery:
    def __init__(
        self,
        *,
        repo: BaseSubscriptionRepo,
        log: BaseEventLog,
        cursors: BaseCursorStore,
        dlq: BaseDeadLetterStore,
        transport: BaseWebhookTransport,
        config: EngineConfig,
        metrics: BaseMetrics | None = None,
    ) -> None:
        self._transport = transport
        self._dispatcher = WebhookDispatcher(repo, transport=transport, config=config)
        self._bus = CatchUpBus(
            log,
            cursors,
            dlq,
            max_handle_attempts=1,  # the sink owns retry+backoff; one strike here → park
            idle_poll=config.delivery_idle_poll,
            metrics=metrics,
            consumer_provider=self._dispatcher.consumers,
        )

    def notify(self) -> None:
        self._bus.notify()

    async def pump_once(self) -> int:
        return await self._bus.pump_once()

    async def run(self, stop: asyncio.Event) -> None:
        try:
            await self._bus.run(stop)
        finally:
            await self._transport.aclose()
