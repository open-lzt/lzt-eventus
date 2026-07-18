"""Repo round-trip + `TokenAccountAdminService` register/verify + `account_alias` fold."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pylzt.client import Client
from pylzt.errors import AuthFailed
from pylzt.lib.clock import FakeClock
from pylzt.types import TokenId

from eventus_fakes import build_fake_engine
from lzt_eventus.account.reconciler import AccountReconciler
from lzt_eventus.account.token_account import TokenAccount, TokenAccountId
from lzt_eventus.config import EngineConfig
from lzt_eventus.delivery.subscription import TransportKind
from lzt_eventus.delivery.subscription_scope import AccountScope
from lzt_eventus.web.base.errors import (
    AliasAlreadyExists,
    AliasNotFound,
    TokenAccountCapExceeded,
    TokenInvalidUpstream,
)
from lzt_eventus.web.repos.subscription_repo import MemorySubscriptionRepo
from lzt_eventus.web.repos.token_account_repo import MemoryTokenAccountRepo
from lzt_eventus.web.schemas.dtos import SubscriptionCreate, TokenAccountCreate
from lzt_eventus.web.services.subscriptions import SubscriptionAdminService
from lzt_eventus.web.services.token_accounts import TokenAccountAdminService
from secret_box import SecretBox

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def test_repo_add_get_by_alias_and_second_alias() -> None:
    repo = MemoryTokenAccountRepo()
    account = TokenAccount(
        account_id=TokenAccountId("acc-1"), token_ciphertext=b"ct", created_at=_NOW
    )

    await repo.add(account, "primary-alias")
    await repo.add_alias(account.account_id, "second-alias")

    by_primary = await repo.get_by_alias("primary-alias")
    by_second = await repo.get_by_alias("second-alias")
    assert by_primary is not None and by_primary.account_id == account.account_id
    assert by_second is not None and by_second.account_id == account.account_id
    aliases = await repo.list_aliases(account.account_id)
    assert {a.alias for a in aliases} == {"primary-alias", "second-alias"}


async def test_repo_duplicate_alias_raises_conflict() -> None:
    repo = MemoryTokenAccountRepo()
    account = TokenAccount(
        account_id=TokenAccountId("acc-1"), token_ciphertext=b"ct", created_at=_NOW
    )
    await repo.add(account, "taken")

    with pytest.raises(AliasAlreadyExists):
        await repo.add_alias(account.account_id, "taken")


class _FakeExecuteClient:
    """Stub swapped in for `pylzt.client.Client` via monkeypatch."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def aclose(self) -> None:
        return None


def _reconciler(repo: MemoryTokenAccountRepo, config: EngineConfig) -> AccountReconciler:
    engine = build_fake_engine(config, client=Client(["engine-boot-token"]), consumers=[])
    return AccountReconciler(
        repo=repo,
        engine=engine,
        secret_box=SecretBox("test-key"),
        min_cadence=1.0,
        max_cadence=60.0,
        cadence=5.0,
    )


def _service(
    repo: MemoryTokenAccountRepo, config: EngineConfig | None = None
) -> TokenAccountAdminService:
    cfg = config or EngineConfig()
    return TokenAccountAdminService(
        repo,
        _reconciler(repo, cfg),
        SecretBox("test-key"),
        cfg,
        clock=FakeClock(start=_NOW),
    )


async def test_register_happy_path_encrypts_and_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = MemoryTokenAccountRepo()
    svc = _service(repo)

    class _OkClient(_FakeExecuteClient):
        async def execute(self, _method: object) -> object:
            return object()

    monkeypatch.setattr(
        "lzt_eventus.web.services.token_accounts.Client", lambda tokens: _OkClient()
    )

    result = await svc.register(TokenAccountCreate(token="raw-token", alias="alias-1"))

    assert result.alias == "alias-1"
    stored = await repo.get(result.account.account_id)
    assert stored is not None
    assert stored.token_ciphertext != b"raw-token"
    assert "_verify" not in stored.metadata


async def test_register_rejects_definitive_401(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = MemoryTokenAccountRepo()
    svc = _service(repo)

    class _RejectingClient(_FakeExecuteClient):
        async def execute(self, _method: object) -> object:
            raise AuthFailed(TokenId("dead-token"))

    monkeypatch.setattr(
        "lzt_eventus.web.services.token_accounts.Client", lambda tokens: _RejectingClient()
    )

    with pytest.raises(TokenInvalidUpstream):
        await svc.register(TokenAccountCreate(token="dead", alias="alias-2"))
    assert await repo.get_by_alias("alias-2") is None


async def test_register_defers_on_timeout_and_still_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = MemoryTokenAccountRepo()
    svc = _service(repo)

    class _TimingOutClient(_FakeExecuteClient):
        async def execute(self, _method: object) -> object:
            raise TimeoutError()

    monkeypatch.setattr(
        "lzt_eventus.web.services.token_accounts.Client", lambda tokens: _TimingOutClient()
    )

    result = await svc.register(TokenAccountCreate(token="flaky", alias="alias-3"))

    stored = await repo.get(result.account.account_id)
    assert stored is not None
    assert stored.metadata["_verify"] == "deferred"


class _OkClient(_FakeExecuteClient):
    async def execute(self, _method: object) -> object:
        return object()


async def test_register_rejects_duplicate_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = MemoryTokenAccountRepo()
    svc = _service(repo)
    monkeypatch.setattr(
        "lzt_eventus.web.services.token_accounts.Client", lambda tokens: _OkClient()
    )

    await svc.register(TokenAccountCreate(token="t1", alias="dup"))
    with pytest.raises(AliasAlreadyExists):
        await svc.register(TokenAccountCreate(token="t2", alias="dup"))


async def test_register_enforces_max_token_accounts_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = MemoryTokenAccountRepo()
    svc = _service(repo, EngineConfig(max_token_accounts=1))
    monkeypatch.setattr(
        "lzt_eventus.web.services.token_accounts.Client", lambda tokens: _OkClient()
    )

    await svc.register(TokenAccountCreate(token="t1", alias="a1"))
    with pytest.raises(TokenAccountCapExceeded):
        await svc.register(TokenAccountCreate(token="t2", alias="a2"))


async def test_subscription_account_alias_folds_into_filters() -> None:
    token_repo = MemoryTokenAccountRepo()
    account = TokenAccount(
        account_id=TokenAccountId("acc-x"), token_ciphertext=b"ct", created_at=_NOW
    )
    await token_repo.add(account, "scoped-alias")

    from eventus_fakes import FakeCursorStore, FakeEventLog, FakeLastSeenStore

    svc = SubscriptionAdminService(
        MemorySubscriptionRepo(),
        FakeCursorStore(),
        FakeEventLog(FakeLastSeenStore()),
        token_accounts=token_repo,
        clock=FakeClock(start=_NOW),
    )

    result = await svc.register(
        SubscriptionCreate(
            transport=TransportKind.POLLING,
            endpoint="source-1",
            event_types=["rating_changed"],
            scope=AccountScope(account_alias="scoped-alias"),
        )
    )

    assert isinstance(result.subscription.scope, AccountScope)
    assert result.subscription.scope.account_alias == "scoped-alias"


async def test_subscription_unknown_account_alias_raises() -> None:
    token_repo = MemoryTokenAccountRepo()
    from eventus_fakes import FakeCursorStore, FakeEventLog, FakeLastSeenStore

    svc = SubscriptionAdminService(
        MemorySubscriptionRepo(),
        FakeCursorStore(),
        FakeEventLog(FakeLastSeenStore()),
        token_accounts=token_repo,
    )

    with pytest.raises(AliasNotFound):
        await svc.register(
            SubscriptionCreate(
                transport=TransportKind.POLLING,
                endpoint="source-1",
                event_types=["rating_changed"],
                scope=AccountScope(account_alias="does-not-exist"),
            )
        )
