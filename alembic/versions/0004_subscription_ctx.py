"""subscription — add ctx (per-transport typed context: polling delay, etc.)

Revision ID: 0004_subscription_ctx
Revises: 0003_token_account
Create Date: 2026-07-08

Existing rows get a webhook-shaped default (server_default), then a data
backfill fixes non-webhook rows to their own transport's empty context — the
column is NOT NULL so every row needs a value that survives the discriminator
check on the way back out through `SubscriptionCtx`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_subscription_ctx"
down_revision: str | None = "0003_token_account"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscription",
        sa.Column(
            "ctx",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text('\'{"kind": "webhook"}\''),
        ),
    )
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE subscription SET ctx = jsonb_build_object('kind', transport) "
            "WHERE transport != 'webhook'"
        )
    )
    op.alter_column("subscription", "ctx", server_default=None)


def downgrade() -> None:
    op.drop_column("subscription", "ctx")
