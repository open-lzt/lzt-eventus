"""Unit tests for scripts/autoupdate.py — the rollout branching logic (e2e gate,
health gate, rollback, maintenance window) gets real pytest coverage here instead
of only ever being exercised live against a running server.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "autoupdate", Path(__file__).resolve().parents[2] / "scripts" / "autoupdate.py"
)
assert _SPEC is not None and _SPEC.loader is not None
autoupdate = importlib.util.module_from_spec(_SPEC)
sys.modules["autoupdate"] = autoupdate
_SPEC.loader.exec_module(autoupdate)


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _fail(stderr: str = "boom") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


class _FakeRun:
    """Records every invocation; per-prefix canned results, default is success."""

    def __init__(self, results: dict[tuple[str, ...], subprocess.CompletedProcess[str]]) -> None:
        self._results = results
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        for prefix, result in self._results.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                return result
        return _ok()


def _config(**overrides: Any) -> Any:
    base = autoupdate.AutoUpdateConfig(enabled=True)
    return autoupdate.AutoUpdateConfig(
        enabled=overrides.get("enabled", base.enabled),
        poll_interval=overrides.get("poll_interval", base.poll_interval),
        git_ref=overrides.get("git_ref", base.git_ref),
        repo_url=overrides.get("repo_url", base.repo_url),
        migrate=overrides.get("migrate", base.migrate),
        e2e_gate=overrides.get("e2e_gate", base.e2e_gate),
        rollback_on_failure=overrides.get("rollback_on_failure", base.rollback_on_failure),
        health_gate=overrides.get("health_gate", base.health_gate),
        window=overrides.get("window", base.window),
        notify=overrides.get("notify", base.notify),
    )


def _runner(
    fake_run: _FakeRun, *, config: Any, health_ok: bool = True, now: datetime | None = None
) -> Any:
    runner = autoupdate.AutoUpdateRunner(
        config,
        run=fake_run,
        http_post=lambda url, payload: None,
        clock=lambda: now or datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    runner._health_gate = lambda: health_ok
    return runner


def test_disabled_config_is_a_noop() -> None:
    fake = _FakeRun({})
    runner = _runner(fake, config=_config(enabled=False))
    runner.run_once()
    assert fake.calls == []


def test_up_to_date_is_a_noop() -> None:
    fake = _FakeRun(
        {
            ("git", "rev-parse", "HEAD"): _ok("abc123\n"),
            ("git", "rev-parse", "FETCH_HEAD"): _ok("abc123\n"),
        }
    )
    runner = _runner(fake, config=_config())
    runner.run_once()
    assert not any(c[:2] == ["git", "merge"] for c in fake.calls)


def test_behind_but_outside_window_defers() -> None:
    fake = _FakeRun(
        {
            ("git", "rev-parse", "HEAD"): _ok("abc123\n"),
            ("git", "rev-parse", "FETCH_HEAD"): _ok("def456\n"),
        }
    )
    window = autoupdate.WindowConfig(start="02:00", end="03:00")
    runner = _runner(
        fake, config=_config(window=window), now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    )
    runner.run_once()
    assert not any(c[:2] == ["git", "merge"] for c in fake.calls)


def test_e2e_gate_failure_reverts_before_migrate() -> None:
    fake = _FakeRun(
        {
            ("git", "rev-parse", "HEAD"): _ok("abc123\n"),
            ("git", "rev-parse", "FETCH_HEAD"): _ok("def456\n"),
            ("uv", "run", "python", "-m", "pytest"): _fail("e2e broke"),
        }
    )
    runner = _runner(fake, config=_config())
    with pytest.raises(autoupdate.E2EGateFailed):
        runner.run_once()
    assert any(c[:3] == ["git", "reset", "--hard"] for c in fake.calls)
    assert not any(c[:2] == ["bash", "scripts/migrate.sh"] for c in fake.calls)
    assert not any("docker" in c for c in fake.calls)


def test_health_gate_failure_triggers_rollback() -> None:
    fake = _FakeRun(
        {
            ("git", "rev-parse", "HEAD"): _ok("abc123\n"),
            ("git", "rev-parse", "FETCH_HEAD"): _ok("def456\n"),
        }
    )
    runner = _runner(fake, config=_config(), health_ok=False)
    with pytest.raises(autoupdate.HealthGateFailed) as exc_info:
        runner.run_once()
    assert exc_info.value.rolled_back is True
    rollback_calls = [c for c in fake.calls if c[:2] == ["bash", "scripts/rollback.sh"]]
    assert rollback_calls == [["bash", "scripts/rollback.sh", "--to", "abc123"]]


def test_health_gate_failure_without_rollback_does_not_roll_back() -> None:
    fake = _FakeRun(
        {
            ("git", "rev-parse", "HEAD"): _ok("abc123\n"),
            ("git", "rev-parse", "FETCH_HEAD"): _ok("def456\n"),
        }
    )
    runner = _runner(fake, config=_config(rollback_on_failure=False), health_ok=False)
    with pytest.raises(autoupdate.HealthGateFailed) as exc_info:
        runner.run_once()
    assert exc_info.value.rolled_back is False
    assert not any(c[:2] == ["bash", "scripts/rollback.sh"] for c in fake.calls)


def test_happy_path_applies_update_and_migrates() -> None:
    fake = _FakeRun(
        {
            ("git", "rev-parse", "HEAD"): _ok("abc123\n"),
            ("git", "rev-parse", "FETCH_HEAD"): _ok("def456\n"),
        }
    )
    runner = _runner(fake, config=_config(), health_ok=True)
    runner.run_once()
    assert ["git", "merge", "--ff-only", "def456"] in fake.calls
    assert ["bash", "scripts/migrate.sh"] in fake.calls
    assert not any(c[:2] == ["bash", "scripts/rollback.sh"] for c in fake.calls)


def test_dirty_tree_falls_back_to_hard_reset() -> None:
    """A non-fast-forwardable working tree (dirty mode bits, diverged history)
    falls back to `git reset --hard`, mirroring update.sh's `merge --ff-only ||
    reset --hard` — dropping this fallback would hard-fail on a real host with
    any local drift (e.g. chmod'd scripts from a prior install)."""
    fake = _FakeRun(
        {
            ("git", "rev-parse", "HEAD"): _ok("abc123\n"),
            ("git", "rev-parse", "FETCH_HEAD"): _ok("def456\n"),
            ("git", "merge", "--ff-only"): _fail("not possible to fast-forward"),
        }
    )
    runner = _runner(fake, config=_config(), health_ok=True)
    runner.run_once()
    assert ["git", "merge", "--ff-only", "def456"] in fake.calls
    assert ["git", "reset", "--hard", "def456"] in fake.calls


def test_migrate_disabled_skips_migration_step() -> None:
    fake = _FakeRun(
        {
            ("git", "rev-parse", "HEAD"): _ok("abc123\n"),
            ("git", "rev-parse", "FETCH_HEAD"): _ok("def456\n"),
        }
    )
    runner = _runner(fake, config=_config(migrate=False), health_ok=True)
    runner.run_once()
    assert not any(c[:2] == ["bash", "scripts/migrate.sh"] for c in fake.calls)


def test_config_load_parses_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "autoupdate.yml"
    config_path.write_text(
        "enabled: true\n"
        "poll_interval: 60\n"
        "git_ref: stable\n"
        "health_gate:\n"
        "  retries: 5\n"
        "window:\n"
        "  start: '01:00'\n"
        "  end: '02:00'\n"
    )
    config = autoupdate.AutoUpdateConfig.load(config_path)
    assert config.enabled is True
    assert config.poll_interval == 60.0
    assert config.git_ref == "stable"
    assert config.health_gate.retries == 5
    assert config.window.start == "01:00"


def test_config_load_defaults_match_shipped_yaml() -> None:
    shipped = Path(__file__).resolve().parents[2] / "deploy" / "autoupdate.yml"
    config = autoupdate.AutoUpdateConfig.load(shipped)
    assert config.enabled is False  # never accidentally ship this flipped on
