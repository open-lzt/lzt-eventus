"""`BaseTokenAccountRepo` — the account-management contract, owned by `account/`.

Concrete implementations (`MemoryTokenAccountRepo`/`PostgresTokenAccountRepo`)
live in `web/repos/token_account_repo.py` since they're wired through the web
admin API — but the contract itself belongs here so `account/` (the reconciler)
never has to import from `web`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from lzt_eventus.account.token_account import Alias, TokenAccount, TokenAccountId


class BaseTokenAccountRepo(ABC):
    @abstractmethod
    async def get(self, account_id: TokenAccountId) -> TokenAccount | None: ...

    @abstractmethod
    async def get_by_alias(self, alias: str) -> TokenAccount | None: ...

    @abstractmethod
    async def alias_exists(self, alias: str) -> bool: ...

    @abstractmethod
    async def add(self, account: TokenAccount, primary_alias: str) -> TokenAccount:
        """Insert the account row + its primary alias row as one unit."""

    @abstractmethod
    async def add_alias(self, account_id: TokenAccountId, alias: str) -> Alias:
        """Attach a non-primary alias to an existing account."""

    @abstractmethod
    async def list_aliases(self, account_id: TokenAccountId) -> Sequence[Alias]: ...

    @abstractmethod
    async def replace(self, account: TokenAccount) -> TokenAccount:
        """Persist metadata/active changes via `dataclasses.replace`."""

    @abstractmethod
    async def list(
        self, *, limit: int, offset: int, active_only: bool = False
    ) -> Sequence[TokenAccount]: ...

    @abstractmethod
    async def count(self, *, active_only: bool = False) -> int: ...

    @abstractmethod
    async def list_active(self) -> Sequence[TokenAccount]:
        """The reconciler's desired-set source — every currently-active account."""
