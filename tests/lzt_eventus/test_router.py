"""EventRouter — decorator-based handler registration over the bus."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from lzt_eventus.consumers.router import EventRouter
from lzt_eventus.events.base import AggregateId, DomainEvent, EventType


def _evt(event_type: EventType) -> DomainEvent:
    return DomainEvent(
        event_id=uuid.UUID(int=1),
        aggregate_id=AggregateId("a"),
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        _event_type=event_type,
    )


async def test_router_dispatches_to_decorated_handlers() -> None:
    router = EventRouter("router-test")
    seen: list[str] = []

    @router.on(EventType.NEW_LOT)
    async def on_new(event: DomainEvent) -> None:
        seen.append("new")

    @router.on(EventType.PRICE_DROPPED)
    async def on_drop(event: DomainEvent) -> None:
        seen.append("drop")

    assert router.wants(_evt(EventType.NEW_LOT))
    assert not router.wants(_evt(EventType.LOT_UPDATED))

    await router.handle(_evt(EventType.NEW_LOT))
    await router.handle(_evt(EventType.PRICE_DROPPED))
    await router.handle(_evt(EventType.LOT_UPDATED))  # no handler → no-op

    assert seen == ["new", "drop"]


async def test_router_one_handler_many_types() -> None:
    router = EventRouter("multi")
    hits = 0

    @router.on(EventType.NEW_LOT, EventType.PRICE_DROPPED)
    async def on_any(event: DomainEvent) -> None:
        nonlocal hits
        hits += 1

    await router.handle(_evt(EventType.NEW_LOT))
    await router.handle(_evt(EventType.PRICE_DROPPED))
    await router.handle(_evt(EventType.LOT_UPDATED))

    assert hits == 2


async def test_router_on_requires_at_least_one_event_type() -> None:
    router = EventRouter("empty")
    with pytest.raises(ValueError, match="at least one"):
        router.on()
