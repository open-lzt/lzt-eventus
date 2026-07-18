"""`WebhookDispatcher` — reconciles active webhook subscriptions into live sinks.

It is the delivery bus's `consumer_provider`: every pump it lists the active webhook
subscriptions as `WebhookTarget`s and defers cache/signature bookkeeping to
`webhook_engine.WebhookDispatcher.reconcile` — the lib owns "rebuild only on a
signature change, drop sinks whose target disappeared"; this adapter only wraps each
resulting lib sink with the lzt_eventus-specific bus metadata (`name`/`subscriptions`).
New subscriptions therefore start delivering within one pump, no restart — the
open-closed seam for runtime-managed egress.
"""

from __future__ import annotations

from lzt_eventus.config import EngineConfig
from lzt_eventus.consumers.consumer import BaseConsumer
from lzt_eventus.delivery.repo import BaseSubscriptionRepo
from lzt_eventus.delivery.sink import WebhookSink, sink_signature
from lzt_eventus.delivery.subscription import Subscription, TransportKind
from webhook_engine.config import WebhookEngineConfig
from webhook_engine.dispatcher import WebhookDispatcher as _LibWebhookDispatcher
from webhook_engine.dispatcher import WebhookTarget
from webhook_engine.transport import BaseWebhookTransport


def _lib_config(config: EngineConfig) -> WebhookEngineConfig:
    webhook = config.webhook
    return WebhookEngineConfig(
        max_attempts=webhook.max_attempts,
        backoff_base=webhook.backoff_base,
        backoff_max=webhook.backoff_max,
        timeout=webhook.timeout,
    )


class WebhookDispatcher:
    def __init__(
        self,
        repo: BaseSubscriptionRepo,
        *,
        transport: BaseWebhookTransport,
        config: EngineConfig,
    ) -> None:
        self._repo = repo
        self._config = config
        # populated as a side effect of _list_targets, consumed by consumers() right after —
        # both run within the same reconcile() call, never interleaved.
        self._subs_by_id: dict[str, Subscription] = {}
        self._lib = _LibWebhookDispatcher(
            list_targets=self._list_targets, transport=transport, config=_lib_config(config)
        )

    async def consumers(self) -> list[BaseConsumer]:
        lib_sinks = await self._lib.reconcile()
        return [
            WebhookSink(self._subs_by_id[sink_id], delegate=lib_sink)
            for sink_id, lib_sink in lib_sinks.items()
        ]

    async def _list_targets(self) -> list[WebhookTarget]:
        subs = await self._active_webhooks()
        self._subs_by_id = {str(sub.subscription_id): sub for sub in subs}
        return [
            WebhookTarget(
                sink_id=str(sub.subscription_id),
                endpoint=sub.endpoint,
                secret=sub.secret,
                signature=sink_signature(sub),
            )
            for sub in subs
        ]

    async def _active_webhooks(self) -> list[Subscription]:
        page = self._config.webhook.max_subscriptions
        out: list[Subscription] = []
        offset = 0
        while True:
            rows = await self._repo.list(limit=page, offset=offset, active_only=True)
            out.extend(s for s in rows if s.transport is TransportKind.WEBHOOK)
            if len(rows) < page:
                return out
            offset += page
