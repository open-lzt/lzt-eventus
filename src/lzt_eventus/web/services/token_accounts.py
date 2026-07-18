"""`TokenAccountAdminService` — register / get-by-alias / metadata / alias / deactivate.

All management logic lives here; routes stay thin (mirrors `SubscriptionAdminService`).
Validates the token against lzt.market at registration (plan Decision 3): one cheap
`GetBalances` read under a short timeout — a definitive 401 (`AuthFailed`) means a
dead credential and raises `TokenInvalidUpstream`; any other transient failure
(timeout, network, other upstream error) persists the account anyway with
`metadata["_verify"]="deferred"` so a good token is never lost to a blip — it stays
active and polls normally; `_verify` is informational only, no automatic re-probe
runs later, and a genuinely dead deferred token surfaces once its source starts
failing. Encrypts at rest via
`secret_box` (Decision 2) and reconciles the poll fleet after every mutation
(Decision 5) for instant effect with no daemon restart.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from uuid import uuid4

import structlog
from pylzt.client import Client
from pylzt.errors import AuthFailed, LztError
from pylzt.methods.balances import GetBalances
from pylzt.types import Category

from lzt_eventus.account.reconciler import AccountReconciler
from lzt_eventus.account.repo import BaseTokenAccountRepo
from lzt_eventus.account.token_account import Alias, TokenAccount, TokenAccountId
from lzt_eventus.config import EngineConfig
from lzt_eventus.lib.clock import Clock, RealClock
from lzt_eventus.web.base.errors import (
    AliasAlreadyExists,
    AliasNotFound,
    TokenAccountCapExceeded,
    TokenAccountNotFound,
    TokenInvalidUpstream,
)
from lzt_eventus.web.base.service import BaseService
from lzt_eventus.web.schemas.dtos import AliasAdd, TokenAccountCreate, TokenAccountUpdate
from secret_box import SecretBox

_log = structlog.get_logger("lzt_eventus.web.token_accounts")

_VALIDATE_TIMEOUT_S = 10.0
_DEFERRED_MARK = "deferred"


class RegisterResult:
    """The created account plus the alias it was registered under."""

    __slots__ = ("account", "alias")

    def __init__(self, account: TokenAccount, alias: str) -> None:
        self.account = account
        self.alias = alias


class TokenAccountAdminService(BaseService):
    def __init__(
        self,
        repo: BaseTokenAccountRepo,
        reconciler: AccountReconciler,
        secret_box: SecretBox,
        config: EngineConfig,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._repo = repo
        self._reconciler = reconciler
        self._secret_box = secret_box
        self._config = config
        self._clock = clock or RealClock()
        # Serializes the cap-check + insert in register(): count() and add() are two
        # separate awaits, so concurrent registrations near the cap boundary could
        # otherwise all pass the check and all insert, exceeding max_token_accounts.
        self._register_lock = asyncio.Lock()

    async def register(self, spec: TokenAccountCreate) -> RegisterResult:
        async with self._register_lock:
            if await self._repo.alias_exists(spec.alias):
                raise AliasAlreadyExists(alias=spec.alias)
            active_count = await self._repo.count(active_only=True)
            if active_count >= self._config.max_token_accounts:
                raise TokenAccountCapExceeded(
                    limit=self._config.max_token_accounts, current=active_count
                )

            metadata = dict(spec.metadata)
            if not await self._validate_upstream(spec.token, alias=spec.alias):
                metadata["_verify"] = _DEFERRED_MARK
                _log.warning("token_validation_deferred", alias=spec.alias)

            account = TokenAccount(
                account_id=TokenAccountId(uuid4().hex),
                token_ciphertext=self._secret_box.encrypt(spec.token),
                created_at=self._clock.now(),
                metadata=metadata,
                categories=tuple(Category.parse(c) for c in spec.categories),
                active=True,
            )
            await self._repo.add(account, spec.alias)
        await self._reconciler.reconcile()
        return RegisterResult(account=account, alias=spec.alias)

    async def _validate_upstream(self, token: str, *, alias: str) -> bool:
        """`True` = confirmed live. `False` = deferred (transient failure, not rejected).

        Raises `TokenInvalidUpstream` only on a definitive 401 — the one case that
        must reject the credential outright rather than defer.
        """
        client = Client([token])
        try:
            async with asyncio.timeout(_VALIDATE_TIMEOUT_S):
                await client.execute(GetBalances())
        except AuthFailed as exc:
            raise TokenInvalidUpstream(alias=alias, reason="upstream rejected the token") from exc
        except (LztError, TimeoutError, OSError) as exc:
            _log.warning("token_validation_transient_error", alias=alias, error=repr(exc))
            return False
        finally:
            await client.aclose()
        return True

    async def get_token_by_alias(self, alias: str) -> tuple[TokenAccount, str]:
        account = await self._repo.get_by_alias(alias)
        if account is None:
            raise AliasNotFound(alias=alias)
        return account, self._secret_box.decrypt(account.token_ciphertext)

    async def get(self, account_id: str) -> TokenAccount:
        account = await self._repo.get(TokenAccountId(account_id))
        if account is None:
            raise TokenAccountNotFound(account_id=account_id)
        return account

    async def add_alias(self, spec: AliasAdd) -> Alias:
        account = await self.get(spec.account_id)
        if await self._repo.alias_exists(spec.alias):
            raise AliasAlreadyExists(alias=spec.alias)
        return await self._repo.add_alias(account.account_id, spec.alias)

    async def list_aliases(self, account_id: str) -> Sequence[Alias]:
        account = await self.get(account_id)
        return await self._repo.list_aliases(account.account_id)

    async def update_metadata(self, spec: TokenAccountUpdate) -> TokenAccount:
        account = await self.get(spec.account_id)
        updated = replace(
            account,
            metadata=dict(spec.metadata) if spec.metadata is not None else account.metadata,
            active=spec.active if spec.active is not None else account.active,
        )
        await self._repo.replace(updated)
        await self._reconciler.reconcile()
        return updated

    async def deactivate(self, account_id: str) -> TokenAccount:
        account = await self.get(account_id)
        updated = replace(account, active=False)
        await self._repo.replace(updated)
        await self._reconciler.reconcile()
        return updated

    async def list_(
        self, *, limit: int, offset: int, active_only: bool = False
    ) -> tuple[Sequence[TokenAccount], int]:
        rows = await self._repo.list(limit=limit, offset=offset, active_only=active_only)
        total = await self._repo.count(active_only=active_only)
        return rows, total
