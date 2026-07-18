"""`last_seen` + `poll_epoch` ORM models — the durable diff baseline.

`last_seen` holds, per (category, item_id), the last price/hash and the durable
`miss_count` (so a restart never drops a pending `LotDisappeared`). `poll_epoch`
stores the per-category poll-cycle counter, bumped in the **same** transaction as
the events it seeds (deterministic-id stability).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from lzt_eventus.orm.base import BaseOrm


class LastSeen(BaseOrm):
    __tablename__ = "last_seen"

    category: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    miss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_polled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PollEpoch(BaseOrm):
    __tablename__ = "poll_epoch"

    category: Mapped[str] = mapped_column(String(64), primary_key=True)
    epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
