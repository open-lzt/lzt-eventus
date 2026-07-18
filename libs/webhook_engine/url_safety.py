"""SSRF guard for outbound webhook URLs — literal-IP and DNS-rebinding defense."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from webhook_engine.errors import UnsafeWebhookUrl

_BLOCKED_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def _is_blocked(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(address in network for network in _BLOCKED_NETWORKS)


def assert_safe_webhook_url(url: str) -> None:
    """Raise UnsafeWebhookUrl if url is not a public https endpoint.

    Resolves the hostname and re-checks every returned address (not just literal
    IPs in the URL string) — a hostname whose current DNS answer is public but
    later rebinds to a blocked range is caught by re-calling this before each
    delivery attempt, not by this function alone.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnsafeWebhookUrl(url=url, reason=f"scheme must be https, got {parts.scheme!r}")

    hostname = parts.hostname
    if not hostname:
        raise UnsafeWebhookUrl(url=url, reason="missing hostname")

    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        literal = None

    if literal is not None:
        if _is_blocked(literal):
            raise UnsafeWebhookUrl(url=url, reason=f"blocked IP range: {literal}")
        return

    try:
        resolved = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise UnsafeWebhookUrl(url=url, reason=f"DNS resolution failed: {exc}") from exc

    for family_info in resolved:
        sockaddr = family_info[4]
        address = ipaddress.ip_address(sockaddr[0])
        if _is_blocked(address):
            raise UnsafeWebhookUrl(url=url, reason=f"resolved to blocked IP range: {address}")
