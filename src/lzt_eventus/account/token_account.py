"""TokenAccount domain model — mirrors `Subscription`'s frozen-dataclass shape.

Two-table normalized model (plan Decision 1): `TokenAccount` owns the encrypted
credential + metadata + lifecycle; `Alias` is a separate addressing entity
(1 account -> N aliases, alias uniqueness DB-enforced via its PRIMARY KEY). The
credential is never held in plaintext here — `token_ciphertext` is Fernet output
from `libs/secret_box`; only the service layer decrypts, and only just-in-time.

Pure domain module: no web, no SQLAlchemy — `account/` owns this contract and
`web/` imports it, not the other way around.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import NewType

from pylzt.types import Category

TokenAccountId = NewType("TokenAccountId", str)


@dataclass(frozen=True, slots=True)
class TokenAccount:
    account_id: TokenAccountId
    token_ciphertext: bytes
    created_at: datetime
    metadata: dict[str, str] = field(default_factory=dict)
    # Advisory only (plan Decision 8) — v1 does not wire per-account category polling.
    categories: tuple[Category, ...] = ()
    active: bool = True


@dataclass(frozen=True, slots=True)
class Alias:
    alias: str
    account_id: TokenAccountId
    created_at: datetime
    is_primary: bool = False
