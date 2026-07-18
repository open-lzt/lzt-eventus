"""subscription — durable store for ws/sse/webhook subscriptions

Revision ID: 0002_subscription
Revises: 0001_event_log
Create Date: 2026-06-29

Holds one row per registered sink. `event_types` is a Postgres text ARRAY and
`filters` a JSONB object (payload-subset match). Secrets are stored as hashes only
(`stream_token_hash`); the webhook `secret` is the retrievable HMAC key.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_subscription"
down_revision: str | None = "0001_event_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscription",
        sa.Column("subscription_id", sa.String(64), primary_key=True),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("endpoint", sa.String(1024), nullable=False),
        sa.Column("event_types", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("filters", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("secret", sa.String(128), nullable=True),
        sa.Column("stream_token_hash", sa.String(128), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_subscription_active", "subscription", ["active"])


def downgrade() -> None:
    op.drop_index("ix_subscription_active", table_name="subscription")
    op.drop_table("subscription")
