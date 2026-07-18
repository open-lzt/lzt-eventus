"""event_log + consumer_cursor + last_seen + poll_epoch + dead_letter

Revision ID: 0001_event_log
Revises:
Create Date: 2026-06-29

`event_log` is a plain (non-partitioned) table with a single-column
`UNIQUE(event_id)`. A partitioned UNIQUE would need `occurred_at` (wall-clock) in
the key and would defeat the deterministic-id crash-replay dedup (D20 > A4), so
growth is bounded by DELETE-based retention (`PgRetentionPruner`) gated on the
consumer watermark, not by dropping partitions.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_event_log"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_log",
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False, server_default=sa.text("''")),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=True),
        sa.UniqueConstraint("event_id", name="uq_event_log_event_id"),
    )
    op.create_index("ix_event_log_occurred_at", "event_log", ["occurred_at"])

    op.create_table(
        "consumer_cursor",
        sa.Column("consumer_name", sa.String(128), primary_key=True),
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "last_seen",
        sa.Column("category", sa.String(64), primary_key=True),
        sa.Column("item_id", sa.BigInteger(), primary_key=True),
        sa.Column("price", sa.Numeric(20, 8), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False, server_default=sa.text("''")),
        sa.Column("miss_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "last_polled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=True),
    )

    op.create_table(
        "poll_epoch",
        sa.Column("category", sa.String(64), primary_key=True),
        sa.Column("epoch", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "dead_letter",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("consumer_name", sa.String(128), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "failed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_dead_letter_consumer_name", "dead_letter", ["consumer_name"])


def downgrade() -> None:
    op.drop_index("ix_dead_letter_consumer_name", table_name="dead_letter")
    op.drop_table("dead_letter")
    op.drop_table("poll_epoch")
    op.drop_table("last_seen")
    op.drop_table("consumer_cursor")
    op.drop_index("ix_event_log_occurred_at", table_name="event_log")
    op.drop_table("event_log")
