"""Plugin contract (open-closed, Law 5).

A consumer declares which event types it wants via `BaseSubscription`s and handles
matching events. A new consumer is a new `BaseConsumer` impl — zero engine edits
(proven by `LoggingConsumer`; the first real consumer is the future `lzt-pulse`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field

from lzt_eventus.events.base import DomainEvent, EventType


@dataclass(frozen=True, slots=True)
class BaseSubscription[E: DomainEvent]:
    """A declared interest: a set of event types + optional payload filters.

    `event_cls` optionally pins a concrete event class (`BaseSubscription[PriceDropped]`)
    so a handler can rely on the narrowed type; it also tightens `matches`.
    """

    event_types: frozenset[EventType]
    filters: Mapping[str, str] = field(default_factory=dict)
    event_cls: type[E] | None = None

    def matches(self, event: DomainEvent) -> bool:
        if event.event_type not in self.event_types:
            return False
        if self.event_cls is not None and not isinstance(event, self.event_cls):
            return False
        return all(str(event.payload.get(k)) == v for k, v in self.filters.items())


class BaseConsumer(ABC):
    """A subscriber. Identified by a stable `name` (its cursor key)."""

    name: str
    subscriptions: list[BaseSubscription[DomainEvent]]

    def wants(self, event: DomainEvent) -> bool:
        return any(sub.matches(event) for sub in self.subscriptions)

    @abstractmethod
    async def handle(self, event: DomainEvent) -> None:
        """Process one event. Raising parks it in the DLQ after max attempts."""
