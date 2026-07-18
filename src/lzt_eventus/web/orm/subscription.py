"""`SubscriptionRow` — the durable Postgres row for a `Subscription`.

The domain dataclass/enum lives in `lzt_eventus.delivery.subscription` (delivery/
owns the contract, web/ just persists it) — this module is SQLAlchemy-only.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lzt_eventus.orm.base import BaseOrm


class SubscriptionRow(BaseOrm):
    """Durable row for a `Subscription` (Postgres backend of the repo)."""

    __tablename__ = "subscription"

    subscription_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(1024), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    scope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    ctx: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    secret: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stream_token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
