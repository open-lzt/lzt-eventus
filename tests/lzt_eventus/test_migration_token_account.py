"""Structural up/down check for `0003_token_account` — no live Postgres in CI.

Mirrors the project's precedent (no `0002_subscription` DB-backed test either):
monkeypatch `alembic.op` to record calls instead of executing DDL, and assert the
upgrade/downgrade shape matches the plan (tables, columns, indexes, and that
downgrade tears down exactly what upgrade built, in reverse order).
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_migration_module() -> ModuleType:
    # `0003_token_account.py` isn't a valid dotted import path (leading digit) —
    # alembic loads revision files this way itself; mirror that here.
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0003_token_account.py"
    spec = importlib.util.spec_from_file_location("token_account_0003", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


migration = _load_migration_module()


@dataclass
class _RecordedOp:
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def create_table(self, name: str, *columns: Any) -> None:
        self.calls.append(("create_table", (name, *columns)))

    def create_index(self, index_name: str, table_name: str, columns: list[str]) -> None:
        self.calls.append(("create_index", (index_name, table_name, columns)))

    def drop_index(self, index_name: str, *, table_name: str) -> None:
        self.calls.append(("drop_index", (index_name, table_name)))

    def drop_table(self, name: str) -> None:
        self.calls.append(("drop_table", (name,)))


@pytest.fixture
def recorded_op(monkeypatch: pytest.MonkeyPatch) -> _RecordedOp:
    fake_op = _RecordedOp()
    monkeypatch.setattr(migration, "op", fake_op)
    return fake_op


def test_revision_chains_after_subscription() -> None:
    assert migration.revision == "0003_token_account"
    assert migration.down_revision == "0002_subscription"


def test_upgrade_creates_both_tables_and_indexes(recorded_op: _RecordedOp) -> None:
    migration.upgrade()

    kinds = [kind for kind, _args in recorded_op.calls]
    assert kinds == ["create_table", "create_index", "create_table", "create_index"]

    table_names = [args[0] for kind, args in recorded_op.calls if kind == "create_table"]
    assert table_names == ["token_account", "token_alias"]

    index_names = [args[0] for kind, args in recorded_op.calls if kind == "create_index"]
    assert index_names == ["ix_token_account_active", "ix_token_alias_account"]


def test_downgrade_reverses_upgrade_exactly(recorded_op: _RecordedOp) -> None:
    migration.upgrade()
    up_calls = list(recorded_op.calls)
    recorded_op.calls.clear()

    migration.downgrade()

    kinds = [kind for kind, _args in recorded_op.calls]
    assert kinds == ["drop_index", "drop_table", "drop_index", "drop_table"]
    dropped_tables = [args[0] for kind, args in recorded_op.calls if kind == "drop_table"]
    created_tables = [args[0] for kind, args in up_calls if kind == "create_table"]
    assert dropped_tables == list(reversed(created_tables))
