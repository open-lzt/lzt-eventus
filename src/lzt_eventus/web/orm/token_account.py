"""TokenAccount ORM rows — SQLAlchemy persistence for `account.token_account`'s domain contract.

Two-table normalized model (plan Decision 1): `TokenAccountRow` owns the encrypted
credential + metadata + lifecycle; `AliasRow` is a separate addressing entity
(1 account -> N aliases, alias uniqueness DB-enforced via its PRIMARY KEY). The
domain dataclasses (`TokenAccount`/`Alias`) live in `lzt_eventus.account.token_account` —
this module holds only the Row classes so `account/` never has to import SQLAlchemy.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lzt_eventus.orm.base import BaseOrm


class TokenAccountRow(BaseOrm):
    """Durable row for a `TokenAccount` (Postgres backend of the repo)."""

    __tablename__ = "token_account"

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Python attr can't be named `metadata` — `DeclarativeBase.metadata` owns that name;
    # the DB column itself is still `metadata` (see the migration).
    metadata_: Mapped[dict[str, str]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    categories: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AliasRow(BaseOrm):
    """Durable row for an `Alias`. `alias` is the PRIMARY KEY — uniqueness is DB-enforced."""

    __tablename__ = "token_alias"

    alias: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("token_account.account_id"), nullable=False
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
