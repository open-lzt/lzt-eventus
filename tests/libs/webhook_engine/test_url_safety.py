from __future__ import annotations

import socket
from typing import Any

import pytest

from webhook_engine.errors import UnsafeWebhookUrl
from webhook_engine.url_safety import assert_safe_webhook_url


def _addrinfo(ip: str) -> list[tuple[Any, ...]]:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, 443))]


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/hook",
        "https://127.5.5.5/hook",
        "https://10.0.0.5/hook",
        "https://172.16.0.1/hook",
        "https://172.31.255.254/hook",
        "https://192.168.1.1/hook",
        "https://169.254.169.254/hook",
        "https://169.254.0.1/hook",
        "https://[::1]/hook",
        "https://[fc00::1]/hook",
        "https://[fdff:ffff::1]/hook",
    ],
)
def test_blocked_literal_ip_ranges(url: str) -> None:
    with pytest.raises(UnsafeWebhookUrl):
        assert_safe_webhook_url(url)


@pytest.mark.parametrize(
    "hostname,ip",
    [
        ("loopback.rebind.test", "127.0.0.1"),
        ("class-a.rebind.test", "10.1.2.3"),
        ("class-b.rebind.test", "172.20.0.1"),
        ("class-c.rebind.test", "192.168.5.5"),
        ("metadata.rebind.test", "169.254.169.254"),
        ("ula.rebind.test", "fc00::5"),
    ],
)
def test_blocked_resolved_dns_ranges(
    monkeypatch: pytest.MonkeyPatch, hostname: str, ip: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo(ip))
    with pytest.raises(UnsafeWebhookUrl):
        assert_safe_webhook_url(f"https://{hostname}/hook")


def test_non_https_scheme_rejected() -> None:
    with pytest.raises(UnsafeWebhookUrl):
        assert_safe_webhook_url("http://example.com/hook")


def test_missing_hostname_rejected() -> None:
    with pytest.raises(UnsafeWebhookUrl):
        assert_safe_webhook_url("https:///hook")


def test_dns_resolution_failure_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args: object, **_kwargs: object) -> list[tuple[Any, ...]]:
        raise OSError("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", _raise)
    with pytest.raises(UnsafeWebhookUrl):
        assert_safe_webhook_url("https://nonexistent.invalid/hook")


def test_allowed_public_https_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))
    assert_safe_webhook_url("https://example.com/hook")  # must not raise
