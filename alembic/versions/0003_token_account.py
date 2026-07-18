"""token_account — durable store for registered lzt.market credentials

Revision ID: 0003_token_account
Revises: 0002_subscription
Create Date: 2026-07-02

Two normalized tables (plan Decision 1): `token_account` owns the encrypted
credential + metadata + lifecycle, `token_alias` is a separate addressing entity
(1 account -> N aliases, uniqueness DB-enforced via its PRIMARY KEY). The credential
is stored as `token_ciphertext` — Fernet output from `libs/secret_box`, never
plaintext (plan Decision 2).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_token_account"
down_revision: str | None = "0002_subscription"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "token_account",
        sa.Column("account_id", sa.String(64), primary_key=True),
        sa.Column("token_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "categories",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_token_account_active", "token_account", ["active"])

    op.create_table(
        "token_alias",
        sa.Column("alias", sa.String(128), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(64),
            sa.ForeignKey("token_account.account_id"),
            nullable=False,
        ),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_token_alias_account", "token_alias", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_token_alias_account", table_name="token_alias")
    op.drop_table("token_alias")
    op.drop_index("ix_token_account_active", table_name="token_account")
    op.drop_table("token_account")
