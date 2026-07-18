"""Decorator-based event-handler registration over the `BaseConsumer` seam.

`EventRouter` is itself a `BaseConsumer` (one bus consumer = one cursor) whose
handlers are bound by `@router.on(...)` instead of by subclassing. Each decorated
coroutine carries a `BaseSubscription`; `handle` fans an event out to every handler
whose subscription matches. Register it like any consumer — `bus.register(router)` or
`engine.add_module(router)`. A handler that raises propagates, so the bus retries
the event for the whole router and parks it in the DLQ past `max_handle_attempts`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from lzt_eventus.consumers.consumer import BaseConsumer, BaseSubscription
from lzt_eventus.events.base import DomainEvent, EventType

EventHandler = Callable[[DomainEvent], Awaitable[None]]


class EventRouter(BaseConsumer):
    """A `BaseConsumer` whose handlers are registered by decorator, not by subclassing."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.subscriptions: list[BaseSubscription[DomainEvent]] = []
        self._routes: list[tuple[BaseSubscription[DomainEvent], EventHandler]] = []

    def on(
        self,
        *event_types: EventType,
        filters: Mapping[str, str] | None = None,
        event_cls: type[DomainEvent] | None = None,
    ) -> Callable[[EventHandler], EventHandler]:
        """Bind a coroutine to one or more event types (optionally payload-filtered).

        `event_cls` pins a concrete event class so the subscription also `isinstance`-
        checks it; the handler can then narrow with a single `assert isinstance(...)`.
        """
        if not event_types:
            raise ValueError("EventRouter.on requires at least one EventType")
        sub: BaseSubscription[DomainEvent] = BaseSubscription(
            event_types=frozenset(event_types),
            filters=dict(filters or {}),
            event_cls=event_cls,
        )

        def register(handler: EventHandler) -> EventHandler:
            self._routes.append((sub, handler))
            self.subscriptions.append(sub)
            return handler

        return register

    async def handle(self, event: DomainEvent) -> None:
        for sub, handler in self._routes:
            if sub.matches(event):
                await handler(event)
