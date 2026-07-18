"""ORM package — declarative models for the durable event-engine stores.

Importing this package registers every table on `BaseOrm.metadata` (Alembic's
`target_metadata`). Layout is flat: one model consumer per table group.
"""

from __future__ import annotations

from lzt_eventus.orm.base import BaseOrm, build_async_sessionmaker
from lzt_eventus.orm.cursor import ConsumerCursor
from lzt_eventus.orm.dead_letter import DeadLetter
from lzt_eventus.orm.event_log import EventLog
from lzt_eventus.orm.last_seen import LastSeen, PollEpoch

__all__ = [
    "BaseOrm",
    "ConsumerCursor",
    "DeadLetter",
    "EventLog",
    "LastSeen",
    "PollEpoch",
    "build_async_sessionmaker",
]
