"""subscription — replace untyped filters with typed scope (none/category/account)

Revision ID: 0005_subscription_scope
Revises: 0004_subscription_ctx
Create Date: 2026-07-08

`filters` was a free-form `dict[str, str]` payload-match; the only two keys it
ever actually carried were `category` (catalog scope) and `account_alias`
(per-account scope) — both now typed variants of `SubscriptionScope`. Backfill
maps the two known keys, everything else collapses to `{"kind": "none"}`
(nothing else was ever set through the API — see `test_subscriptions_api.py`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_subscription_scope"
down_revision: str | None = "0004_subscription_ctx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscription",
        sa.Column(
            "scope",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text('\'{"kind": "none"}\''),
        ),
    )
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE subscription SET scope = CASE
                WHEN filters ? 'category' THEN
                    jsonb_build_object('kind', 'category', 'category', filters->>'category')
                WHEN filters ? 'account_alias' THEN
                    jsonb_build_object('kind', 'account', 'account_alias', filters->>'account_alias')
                ELSE '{"kind": "none"}'::jsonb
            END
            """
        )
    )
    op.alter_column("subscription", "scope", server_default=None)
    op.drop_column("subscription", "filters")


def downgrade() -> None:
    op.add_column(
        "subscription",
        sa.Column("filters", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
    )
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE subscription SET filters = CASE
                WHEN scope->>'kind' = 'category' THEN
                    jsonb_build_object('category', scope->>'category')
                WHEN scope->>'kind' = 'account' THEN
                    jsonb_build_object('account_alias', scope->>'account_alias')
                ELSE '{}'::jsonb
            END
            """
        )
    )
    op.alter_column("subscription", "filters", server_default=None)
    op.drop_column("subscription", "scope")
