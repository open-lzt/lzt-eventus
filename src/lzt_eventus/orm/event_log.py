"""`event_log` ORM model — the durable append-only log table.

Not partitioned. A partitioned UNIQUE would have to include the partition key
(`occurred_at`), which the source stamps from wall-clock — so a crash-replay of the
same logical poll would produce a different `occurred_at`, dodge the UNIQUE, and
double-emit. Keeping `event_id` a plain single-column UNIQUE restores the
deterministic-id crash-replay dedup (D20 > A4); growth is bounded by DELETE-based
retention (`PgRetentionPruner`) instead of dropping partitions.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Uuid
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lzt_eventus.events.base import EventType
from lzt_eventus.orm.base import BaseOrm

# VARCHAR(64) storing the enum *value* (`item_sold`, not `ITEM_SOLD`) — same bytes as
# the old String(64) column, so no DDL/migration change. `native_enum=False` keeps it a
# plain VARCHAR (no PG ENUM type → no ALTER TYPE when the catalog grows) and
# `create_constraint=False` drops the CHECK (a stale CHECK would reject a newly-added
# member); the Python attribute is still typed `EventType`.
_EVENT_TYPE_COLUMN = SQLEnum(
    EventType,
    native_enum=False,
    create_constraint=False,
    length=64,
    values_callable=lambda enum: [member.value for member in enum],
)


class EventLog(BaseOrm):
    __tablename__ = "event_log"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # single-column UNIQUE — no occurred_at in the key (would defeat D20 dedup)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    event_type: Mapped[EventType] = mapped_column(_EVENT_TYPE_COLUMN, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
