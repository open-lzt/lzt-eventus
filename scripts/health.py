#!/usr/bin/env python3
"""Standalone health probe — curls /healthz + /readyz, exits non-zero on failure.

stdlib only (no project import), so it works as an update/health gate before the
package is even synced. Used by update.sh, install.sh and restart.sh.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request


def _base() -> str:
    host = os.environ.get("LZT_HEALTH_HOST", "127.0.0.1")
    if host == "0.0.0.0":  # bind-all is not a dial target
        host = "127.0.0.1"
    port = os.environ.get("LZT_HEALTH_PORT", "27543")
    return f"http://{host}:{port}"


def _probe(url: str, timeout: float) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            code = resp.getcode()
            return (200 <= code < 300, f"HTTP {code}")
    except urllib.error.HTTPError as exc:
        return (False, f"HTTP {exc.code}")
    except (urllib.error.URLError, OSError) as exc:
        return (False, str(exc.reason if isinstance(exc, urllib.error.URLError) else exc))


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe the daemon's /healthz + /readyz.")
    ap.add_argument("--base", default=_base(), help="base URL (default from LZT_HEALTH_*)")
    ap.add_argument("--retries", type=int, default=1, help="attempts before giving up")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between retries")
    ap.add_argument("--timeout", type=float, default=3.0, help="per-request timeout")
    ap.add_argument(
        "--require-ready",
        action="store_true",
        default=True,
        help="also require /readyz (deps up), not just liveness",
    )
    args = ap.parse_args()

    endpoints = ["/healthz"] + (["/readyz"] if args.require_ready else [])
    for attempt in range(1, args.retries + 1):
        results = [(ep, *_probe(f"{args.base}{ep}", args.timeout)) for ep in endpoints]
        if all(ok for _, ok, _ in results):
            for ep, _, detail in results:
                print(f"ok   {ep} {detail}")
            return 0
        if attempt < args.retries:
            time.sleep(args.interval)

    for ep, ok, detail in results:
        print(f"{'ok  ' if ok else 'FAIL'} {ep} {detail}", file=sys.stderr)
    print(f"health gate failed after {args.retries} attempt(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
