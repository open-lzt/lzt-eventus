#!/usr/bin/env python3
"""Config-driven rolling auto-updater — Python runner (replaces autoupdate.sh).

Reads deploy/autoupdate.yml. Each cycle: git fetch -> compare local vs tracked
ref -> if behind and inside the maintenance window: apply code -> uv sync ->
e2e gate (pytest -m e2e, pre-swap — a failure reverts the checkout and never
touches the DB/live daemon) -> compose build -> scripts/migrate.sh ->
compose up -> health gate -> scripts/rollback.sh on health failure.

Reuses scripts/migrate.sh and scripts/rollback.sh for the actual DB/container
mutations (their docker-compose --project-directory path handling is already
correct) and reimplements the rest — git plumbing, gates, window check,
notify, poll loop — in Python so the branching logic (which bash could only
ever get live-fire-tested) has real unit-test coverage.

Run as a loop (--daemon) or a single pass (--once, for the systemd timer).
"""

from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

RunFn = Callable[..., subprocess.CompletedProcess[str]]
HttpPostFn = Callable[[str, dict[str, str]], None]
ClockFn = Callable[[], datetime]


class AutoUpdateError(Exception):
    """Root of the runner's typed error tree."""


class CommandFailed(AutoUpdateError):
    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"command failed ({returncode}): {' '.join(cmd)}")


class E2EGateFailed(AutoUpdateError):
    def __init__(self, prev_sha: str) -> None:
        self.prev_sha = prev_sha
        super().__init__(f"e2e gate failed — reverted to {prev_sha}, live service untouched")


class HealthGateFailed(AutoUpdateError):
    def __init__(self, new_sha: str, rolled_back: bool) -> None:
        self.new_sha = new_sha
        self.rolled_back = rolled_back
        super().__init__(f"health gate failed at {new_sha} (rolled_back={rolled_back})")


@dataclass(frozen=True, slots=True)
class HealthGateConfig:
    url: str = "http://127.0.0.1:27543/healthz"
    timeout: float = 3.0
    retries: int = 30
    interval: float = 2.0


_HEALTH_GATE_DEFAULT = HealthGateConfig()


@dataclass(frozen=True, slots=True)
class WindowConfig:
    start: str = ""
    end: str = ""


@dataclass(frozen=True, slots=True)
class NotifyConfig:
    webhook: str = ""


@dataclass(frozen=True, slots=True)
class AutoUpdateConfig:
    enabled: bool = False
    poll_interval: float = 300.0
    git_ref: str = "master"
    repo_url: str = "origin"
    migrate: bool = True
    e2e_gate: bool = True
    rollback_on_failure: bool = True
    health_gate: HealthGateConfig = field(default_factory=HealthGateConfig)
    window: WindowConfig = field(default_factory=WindowConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    @classmethod
    def load(cls, path: Path) -> AutoUpdateConfig:
        raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
        health_raw = raw.get("health_gate") or {}
        window_raw = raw.get("window") or {}
        notify_raw = raw.get("notify") or {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            poll_interval=float(raw.get("poll_interval", 300.0)),
            git_ref=str(raw.get("git_ref", "master")),
            repo_url=str(raw.get("repo_url", "origin")),
            migrate=bool(raw.get("migrate", True)),
            e2e_gate=bool(raw.get("e2e_gate", True)),
            rollback_on_failure=bool(raw.get("rollback_on_failure", True)),
            health_gate=HealthGateConfig(
                url=str(health_raw.get("url", _HEALTH_GATE_DEFAULT.url)),
                timeout=float(health_raw.get("timeout", _HEALTH_GATE_DEFAULT.timeout)),
                retries=int(health_raw.get("retries", _HEALTH_GATE_DEFAULT.retries)),
                interval=float(health_raw.get("interval", _HEALTH_GATE_DEFAULT.interval)),
            ),
            window=WindowConfig(
                start=str(window_raw.get("start", "")), end=str(window_raw.get("end", ""))
            ),
            notify=NotifyConfig(webhook=str(notify_raw.get("webhook", ""))),
        )


def _default_run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def _default_http_post(url: str, payload: dict[str, str]) -> None:
    import json

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with contextlib.suppress(urllib.error.URLError, OSError):
        urllib.request.urlopen(req, timeout=10)  # best-effort notification, never blocks rollout


def _default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class UpdateCheck:
    local_sha: str
    remote_sha: str

    @property
    def up_to_date(self) -> bool:
        return self.local_sha == self.remote_sha


class AutoUpdateRunner:
    def __init__(
        self,
        config: AutoUpdateConfig,
        *,
        repo_root: Path = REPO_ROOT,
        run: RunFn = _default_run,
        http_post: HttpPostFn = _default_http_post,
        clock: ClockFn = _default_clock,
    ) -> None:
        self._config = config
        self._repo_root = repo_root
        self._run = run
        self._http_post = http_post
        self._clock = clock

    def _sh(self, *cmd: str) -> subprocess.CompletedProcess[str]:
        result = self._run(list(cmd), cwd=self._repo_root)
        if result.returncode != 0:
            raise CommandFailed(list(cmd), result.returncode, result.stderr)
        return result

    def _git_rev_parse(self, ref: str) -> str:
        return self._sh("git", "rev-parse", ref).stdout.strip()

    def check(self) -> UpdateCheck:
        self._run(
            ["git", "fetch", "--tags", "--prune", self._config.repo_url, self._config.git_ref],
            cwd=self._repo_root,
        )
        local_sha = self._git_rev_parse("HEAD")
        try:
            remote_sha = self._git_rev_parse("FETCH_HEAD")
        except CommandFailed:
            remote_sha = self._git_rev_parse(f"{self._config.repo_url}/{self._config.git_ref}")
        return UpdateCheck(local_sha=local_sha, remote_sha=remote_sha)

    def in_window(self) -> bool:
        start, end = self._config.window.start, self._config.window.end
        if not start or not end:
            return True
        now = self._clock().strftime("%H:%M")
        if start < end:
            return start < now < end
        return now > start or now < end  # window crosses midnight

    def notify(self, message: str) -> None:
        if not self._config.notify.webhook:
            return
        self._http_post(self._config.notify.webhook, {"text": f"[lzt-core autoupdate] {message}"})

    def _health_gate(self) -> bool:
        hg = self._config.health_gate
        for attempt in range(1, hg.retries + 1):
            try:
                with urllib.request.urlopen(hg.url, timeout=hg.timeout) as resp:
                    if 200 <= resp.getcode() < 300:
                        return True
            except (urllib.error.URLError, OSError):
                pass
            if attempt < hg.retries:
                time.sleep(hg.interval)
        return False

    def _apply_ref(self, new_sha: str) -> None:
        result = self._run(["git", "merge", "--ff-only", new_sha], cwd=self._repo_root)
        if result.returncode == 0:
            return
        # Mirrors update.sh: fast-forward is the common case, but a dirty
        # working tree (e.g. mode-bit drift from a prior install) or a
        # diverged history falls back to a hard reset onto the target sha.
        self._sh("git", "reset", "--hard", new_sha)

    def roll_out(self, check: UpdateCheck) -> None:
        prev_sha, new_sha = check.local_sha, check.remote_sha
        self.notify(f"rolling update {prev_sha} -> {new_sha} started")

        self._apply_ref(new_sha)
        self._sh("uv", "sync", "--extra", "engine", "--extra", "dev")

        if self._config.e2e_gate:
            e2e = self._run(
                ["uv", "run", "python", "-m", "pytest", "-m", "e2e", "-q"], cwd=self._repo_root
            )
            if e2e.returncode != 0:
                self._sh("git", "reset", "--hard", prev_sha)
                self._run(
                    ["uv", "sync", "--extra", "engine"],
                    cwd=self._repo_root,
                )
                self.notify(f"update ABORTED by e2e gate for {new_sha} — reverted to {prev_sha}")
                raise E2EGateFailed(prev_sha)

        self._sh_compose_build()
        if self._config.migrate:
            self._sh("bash", "scripts/migrate.sh")
        self._sh_compose_up()

        if self._health_gate():
            self.notify(f"update OK -> {new_sha}")
            return

        rolled_back = False
        if self._config.rollback_on_failure:
            self._sh("bash", "scripts/rollback.sh", "--to", prev_sha)
            rolled_back = True
        self.notify(f"update FAILED for {new_sha} (rolled_back={rolled_back})")
        raise HealthGateFailed(new_sha, rolled_back)

    def _compose(self, *args: str) -> None:
        # Mirrors scripts/_lib.sh's compose(): NO --project-directory. Compose
        # derives the project directory from the -f file's own location (deploy/),
        # which is what docker-compose.yml's `context: ..` is anchored to — passing
        # --project-directory=<repo_root> here shifts that anchor and breaks the
        # build context path (verified live: caused a real prod build failure).
        self._sh(
            "docker",
            "compose",
            "-f",
            str(self._repo_root / "deploy" / "docker-compose.yml"),
            "--env-file",
            str(self._repo_root / ".env"),
            *args,
        )

    def _sh_compose_build(self) -> None:
        self._compose("build", "engine")

    def _sh_compose_up(self) -> None:
        self._compose("up", "-d", "engine")

    def run_once(self) -> None:
        if not self._config.enabled:
            print(f"auto-update disabled — skipping ({self._clock().isoformat()})")
            return
        check = self.check()
        if check.up_to_date:
            print(f"up to date ({check.local_sha})")
            return
        print(f"behind: local {check.local_sha} -> remote {check.remote_sha}")
        if not self.in_window():
            print("outside maintenance window — deferring update")
            return
        self.roll_out(check)

    def run_forever(self) -> None:
        print(f"auto-updater loop started (poll_interval={self._config.poll_interval}s)")
        while True:
            try:
                self.run_once()
            except AutoUpdateError as exc:
                print(f"auto-update pass errored (continuing): {exc}", file=sys.stderr)
            time.sleep(self._config.poll_interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Poll a git ref and roll out updates per deploy/autoupdate.yml."
    )
    parser.add_argument("--once", action="store_true", help="single check-and-maybe-update pass")
    parser.add_argument("--daemon", action="store_true", help="loop forever (default)")
    parser.add_argument(
        "--config", type=Path, default=REPO_ROOT / "deploy" / "autoupdate.yml", help="config path"
    )
    args = parser.parse_args(argv)

    config = AutoUpdateConfig.load(args.config)
    runner = AutoUpdateRunner(config)

    try:
        if args.once:
            runner.run_once()
        else:
            runner.run_forever()
    except AutoUpdateError as exc:
        print(f"auto-update failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
