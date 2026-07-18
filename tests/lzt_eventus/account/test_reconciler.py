"""`AccountReconciler.reconcile()` against a live `build_fake_engine` +
`seed_lzt_tokens` boot-idempotency."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from pylzt.client import Client
from pylzt.lib.clock import FakeClock
from pydantic import SecretStr

from eventus_fakes import build_fake_engine
from lzt_eventus.account.reconciler import AccountReconciler, seed_lzt_tokens
from lzt_eventus.account.token_account import TokenAccount, TokenAccountId
from lzt_eventus.config import EngineConfig
from lzt_eventus.engine import EventEngine
from lzt_eventus.web.repos.token_account_repo import MemoryTokenAccountRepo
from secret_box import SecretBox

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _reconciler(
    repo: MemoryTokenAccountRepo, engine: EventEngine, box: SecretBox
) -> AccountReconciler:
    return AccountReconciler(
        repo=repo, engine=engine, secret_box=box, min_cadence=1.0, max_cadence=60.0, cadence=5.0
    )


async def test_reconcile_adds_rating_poller_on_register_and_removes_on_deactivate() -> None:
    repo = MemoryTokenAccountRepo()
    engine = build_fake_engine(EngineConfig(), client=Client(["boot"]), consumers=[])
    box = SecretBox("test-key")
    reconciler = _reconciler(repo, engine, box)

    account = TokenAccount(
        account_id=TokenAccountId("acc-1"),
        token_ciphertext=box.encrypt("raw-token"),
        created_at=_NOW,
    )
    await repo.add(account, "alias-1")

    before = set(engine.source_names)
    await reconciler.reconcile()
    after_register = set(engine.source_names)

    assert after_register - before == {"account:acc-1:rating"}

    await repo.replace(replace(account, active=False))
    await reconciler.reconcile()
    after_deactivate = set(engine.source_names)

    assert "account:acc-1:rating" not in after_deactivate


async def test_reconcile_is_idempotent_no_duplicate_poller_error() -> None:
    repo = MemoryTokenAccountRepo()
    engine = build_fake_engine(EngineConfig(), client=Client(["boot"]), consumers=[])
    box = SecretBox("test-key")
    reconciler = _reconciler(repo, engine, box)

    account = TokenAccount(
        account_id=TokenAccountId("acc-2"),
        token_ciphertext=box.encrypt("raw-token"),
        created_at=_NOW,
    )
    await repo.add(account, "alias-2")

    await reconciler.reconcile()
    await reconciler.reconcile()  # unchanged signature — must not re-add / raise

    assert engine.source_names.count("account:acc-2:rating") == 1


async def test_reconcile_rebuilds_poller_when_signature_changes() -> None:
    repo = MemoryTokenAccountRepo()
    engine = build_fake_engine(EngineConfig(), client=Client(["boot"]), consumers=[])
    box = SecretBox("test-key")
    reconciler = _reconciler(repo, engine, box)

    account = TokenAccount(
        account_id=TokenAccountId("acc-3"),
        token_ciphertext=box.encrypt("raw-token"),
        created_at=_NOW,
    )
    await repo.add(account, "alias-3")
    await reconciler.reconcile()

    await repo.replace(replace(account, metadata={"note": "changed"}))
    await reconciler.reconcile()

    assert engine.source_names.count("account:acc-3:rating") == 1


async def test_seed_lzt_tokens_registers_env_tokens_idempotently() -> None:
    repo = MemoryTokenAccountRepo()
    box = SecretBox("test-key")
    config = EngineConfig(tokens=[SecretStr("env-token-a"), SecretStr("env-token-b")])
    clock = FakeClock(start=_NOW)

    await seed_lzt_tokens(config, repo, box, clock=clock)
    first_count = await repo.count()

    await seed_lzt_tokens(config, repo, box, clock=clock)
    second_count = await repo.count()

    assert first_count == 2
    assert second_count == 2  # second boot is a no-op — aliases already exist
    assert await repo.alias_exists("env-0")
    assert await repo.alias_exists("env-1")
