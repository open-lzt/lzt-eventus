"""Boots the sibling `lzt-testnet` mock marketplace as a subprocess for integration tests.

`lzt-testnet` is a separate repo/process (not a Python dependency of this package), so the
only correct integration boundary is a subprocess speaking real HTTP — never an in-process
import of `lzt_testnet`.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

def _resolve_testnet_repo() -> Path:
    """Locate the lzt-testnet package dir. Prefer the monorepo sibling (`projects/testnet`);
    fall back to the standalone sibling clone so the fixture works in both layouts."""
    monorepo = Path(__file__).resolve().parents[3] / "testnet"
    if (monorepo / "pyproject.toml").exists():
        return monorepo
    return Path(r"C:\Users\User\Desktop\lzt-testnet")


_LZT_TESTNET_REPO = _resolve_testnet_repo()
_STARTUP_TIMEOUT_S = 30.0
_STARTUP_POLL_INTERVAL_S = 0.1


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def testnet_server() -> Iterator[str]:
    """Boots `lzt_testnet.api.app:create_app` as a subprocess for the duration of one test.

    Skips (not fails) if the sibling repo or the `uv` executable isn't present — this fixture
    is opt-in integration infra, not a hard dependency for the rest of the suite.

    Yields:
        The base URL (`http://127.0.0.1:<port>`) of the running mock server.
    """
    if not _LZT_TESTNET_REPO.exists():
        pytest.skip(f"lzt-testnet repo not found at {_LZT_TESTNET_REPO}")
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        pytest.skip("uv executable not found on PATH")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            uv_executable,
            "run",
            "uvicorn",
            "lzt_testnet.api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(_LZT_TESTNET_REPO),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_until_ready(process, base_url)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)


def _wait_until_ready(process: subprocess.Popen[bytes], base_url: str) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    with httpx.Client(timeout=2.0) as probe:
        while True:
            if process.poll() is not None:
                pytest.fail(f"lzt-testnet subprocess exited early (code={process.returncode})")
            try:
                response = probe.get(f"{base_url}/testnet/health")
                if response.status_code == httpx.codes.OK:
                    return
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f"lzt-testnet did not become ready within {_STARTUP_TIMEOUT_S}s")
            time.sleep(_STARTUP_POLL_INTERVAL_S)
