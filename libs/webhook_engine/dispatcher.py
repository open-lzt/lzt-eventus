"""`WebhookDispatcher` — reconciles a list of webhook targets into live `WebhookSink`s.

Caches one `WebhookSink` per `sink_id`, rebuilding only when the target's opaque
`signature` changed (e.g. endpoint/secret/active flip), and drops sinks whose target
disappeared. Framework-agnostic: the host supplies `list_targets` (however it stores
subscriptions) and consumes the resulting sink map — no event-bus or repo coupling.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass

from webhook_engine.config import WebhookEngineConfig
from webhook_engine.sink import WebhookSink
from webhook_engine.transport import BaseWebhookTransport


@dataclass(frozen=True, slots=True)
class WebhookTarget:
    """One delivery destination — the host maps its own subscription model to this."""

    sink_id: str
    endpoint: str
    secret: str | None
    signature: Hashable  # opaque; sink is rebuilt only when this changes


class WebhookDispatcher:
    def __init__(
        self,
        *,
        list_targets: Callable[[], Awaitable[list[WebhookTarget]]],
        transport: BaseWebhookTransport,
        config: WebhookEngineConfig,
    ) -> None:
        self._list_targets = list_targets
        self._transport = transport
        self._config = config
        self._cache: dict[str, tuple[Hashable, WebhookSink]] = {}

    async def reconcile(self) -> dict[str, WebhookSink]:
        targets = await self._list_targets()
        live: set[str] = set()
        out: dict[str, WebhookSink] = {}
        for target in targets:
            live.add(target.sink_id)
            cached = self._cache.get(target.sink_id)
            if cached is None or cached[0] != target.signature:
                sink = WebhookSink(
                    sink_id=target.sink_id,
                    endpoint=target.endpoint,
                    secret=target.secret,
                    transport=self._transport,
                    config=self._config,
                )
                self._cache[target.sink_id] = (target.signature, sink)
            out[target.sink_id] = self._cache[target.sink_id][1]
        for stale in self._cache.keys() - live:
            del self._cache[stale]
        return out
