"""Smoke test proving `testnet_server` genuinely boots and tears down the mock marketplace."""

from __future__ import annotations

import httpx
import pytest

from fixtures.testnet_server import testnet_server

pytestmark = pytest.mark.e2e

__all__ = ["testnet_server"]


def test_testnet_server_health(testnet_server: str) -> None:
    response = httpx.get(f"{testnet_server}/testnet/health", timeout=5.0)
    assert response.status_code == httpx.codes.OK
