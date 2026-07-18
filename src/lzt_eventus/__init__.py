"""lzt_eventus — poll → domain events → durable replayable log → catch-up bus.

`import lzt_eventus` performs zero I/O (Law 23): the SQLAlchemy/Postgres stores
and the web app are imported lazily only when the daemon is actually built.
"""

from __future__ import annotations

from lzt_eventus.config import EngineConfig
from lzt_eventus.consumers.consumer import BaseConsumer, BaseSubscription
from lzt_eventus.engine import EventEngine, Stores
from lzt_eventus.events import DomainEvent, EventType

__all__ = [
    "BaseConsumer",
    "BaseSubscription",
    "DomainEvent",
    "EngineConfig",
    "EventEngine",
    "EventType",
    "Stores",
]
