"""`AccountReconciler` — diffs desired (repo, active accounts) vs live (engine sources).

Mirrors `WebhookDispatcher.reconcile()` (`libs/webhook_engine/dispatcher.py`): a
per-account source set is rebuilt only when its signature changes, and torn down
when the account disappears from the desired set. Hooks the engine's existing
runtime seam (`add_source`/`remove_source` -> `SourceManager`, which already
supervises restart-with-backoff) — no new lifecycle machinery.

v1 wires exactly one per-account source kind — `RatingSource`, the sole
account-scoped source today (`sources/rating.py`). Payments/notifications/
conversations/guarantee land in `.plans/event-sources-expansion/`; they plug in
by extending `_build_account_sources` with no other reconciler change, per the
plan's Risks section.

`reconcile()` is serialized by `self._lock`: it's invoked both from a periodic
safety-sweep loop (`engine.py::_reconcile_loop`) and, for instant effect, after
every admin mutation (`TokenAccountAdminService.register/update_metadata/
deactivate`). Without serialization, two overlapping passes can both decide the
same account's source is missing (the desired-vs-cache diff spans several
`await` points) and both call `engine.add_source(...)` for the same name, which
raises `DuplicateSource` — surfacing as a misleading 500 on an admin request
whose DB write already succeeded, or silently aborting the whole periodic sweep.
`add_source`/`remove_source` are also wrapped defensively as a second line of
defense against any residual name collision.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Hashable
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from pylzt.client import Client

from lzt_eventus.account.token_account import TokenAccount, TokenAccountId
from lzt_eventus.errors import DuplicateSource, SourceNotFound
from lzt_eventus.sources.rating import RatingSource
from lzt_eventus.transport import LogTransport

if TYPE_CHECKING:
    from lzt_eventus.account.repo import BaseTokenAccountRepo
    from lzt_eventus.config import EngineConfig
    from lzt_eventus.engine import EventEngine
    from lzt_eventus.lib.clock import Clock
    from secret_box import SecretBox

_log = structlog.get_logger("lzt_eventus.account.reconciler")


def _signature(account: TokenAccount) -> Hashable:
    meta_items = tuple(sorted(account.metadata.items()))
    payload = f"{account.account_id}:{account.token_ciphertext!r}:{meta_items}:{account.active}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _rating_source_name(account_id: TokenAccountId) -> str:
    return f"account:{account_id}:rating"


class AccountReconciler:
    def __init__(
        self,
        *,
        repo: BaseTokenAccountRepo,
        engine: EventEngine,
        secret_box: SecretBox,
        min_cadence: float,
        max_cadence: float,
        cadence: float,
    ) -> None:
        self._repo = repo
        self._engine = engine
        self._secret_box = secret_box
        self._min_cadence = min_cadence
        self._max_cadence = max_cadence
        self._cadence = cadence
        # Cache of the signature each account's live source set was built from —
        # empty until the first reconcile(), so a fresh process always rebuilds once.
        self._cache: dict[TokenAccountId, Hashable] = {}
        self._lock = asyncio.Lock()

    async def reconcile(self) -> None:
        async with self._lock:
            await self._reconcile_locked()

    async def _reconcile_locked(self) -> None:
        accounts = await self._repo.list_active()
        desired: dict[TokenAccountId, TokenAccount] = {a.account_id: a for a in accounts}

        for account_id, account in desired.items():
            signature = _signature(account)
            if self._cache.get(account_id) == signature:
                continue
            if account_id in self._cache:
                self._remove_source_safe(_rating_source_name(account_id))
            await self._add_account_sources(account)
            self._cache[account_id] = signature

        for stale_id in set(self._cache) - set(desired):
            self._remove_source_safe(_rating_source_name(stale_id))
            del self._cache[stale_id]

    def _remove_source_safe(self, name: str) -> None:
        try:
            self._engine.remove_source(name)
        except SourceNotFound:
            _log.warning("reconcile_remove_source_not_found", name=name)

    async def _add_account_sources(self, account: TokenAccount) -> None:
        try:
            token = self._secret_box.decrypt(account.token_ciphertext)
        except Exception:
            _log.exception("account_decrypt_failed", account_id=account.account_id)
            return  # never poll with a token we can't decrypt; next reconcile retries
        alias = await self._primary_alias(account.account_id)
        client = Client([token])
        stores = self._engine.stores
        source = RatingSource(
            client=client,
            transport=LogTransport(stores.log, on_committed=self._engine.bus.notify),
            last_seen=stores.last_seen,
            min_cadence=self._min_cadence,
            max_cadence=self._max_cadence,
            cadence=self._cadence,
            account_alias=alias,
        )
        source.name = _rating_source_name(account.account_id)
        try:
            self._engine.add_source(source)
        except DuplicateSource:
            _log.warning("reconcile_add_source_duplicate", name=source.name)

    async def _primary_alias(self, account_id: TokenAccountId) -> str | None:
        for row in await self._repo.list_aliases(account_id):
            if row.is_primary:
                return row.alias
        return None


async def seed_lzt_tokens(
    config: EngineConfig,
    repo: BaseTokenAccountRepo,
    secret_box: SecretBox,
    *,
    clock: Clock,
) -> None:
    """Idempotently register each `LZT_TOKENS` entry as a `TokenAccount` under alias `env-{i}`.

    Non-breaking `LZT_TOKENS` deprecation (plan Decision 7) — a running daemon must
    not lose its tokens on upgrade. Skips an index whose alias already exists, so
    it's safe to call on every boot. No upstream validation here (fast boot path,
    distinct from `TokenAccountAdminService.register()`'s validate-at-registration
    flow); a dead env token surfaces the same way any dead account would once the
    per-account sources start failing.
    """
    for i, token in enumerate(config.tokens):
        alias = f"env-{i}"
        if await repo.alias_exists(alias):
            continue
        _log.warning("lzt_tokens_env_var_deprecated", alias=alias)
        account = TokenAccount(
            account_id=TokenAccountId(uuid4().hex),
            token_ciphertext=secret_box.encrypt(token.get_secret_value()),
            created_at=clock.now(),
        )
        await repo.add(account, alias)
