# Error handling

<p align="right"><b>English</b> · <a href="errors.md">Русский</a></p>

Every upstream signal is narrowed to exactly one typed `LztError` subclass carrying
**structured args** (never a pre-formatted string), so you branch on a type, not on a
status code or a message. One mapping owns the upstream vocabulary; nothing downstream
inspects a raw status.

## The tree

```
LztError                       code: ErrorCode          raised when
├─ RateLimited(retry_after)    RATE_LIMITED             429 — pool honours Retry-After
├─ RetryableUpstream(hint)     RETRY_REQUEST            transient upstream, retry
├─ CaptchaRequired()           CAPTCHA                  a captcha gate
├─ ProxyChallenge(kind)        STEAM_CAPTCHA            exit IP challenged → rotate proxy
├─ AuthFailed(token_id)        UNAUTHORIZED             401 — token quarantined
├─ Forbidden(scope)            FORBIDDEN                403
├─ NotFound(item_id)           NOT_FOUND                404
├─ BadRequest(field)           BAD_REQUEST              4xx client error
├─ TransportError(status)      UPSTREAM_ERROR           5xx
├─ DependencyMissing(extra)    DEP_MISSING              optional backend not installed
├─ MethodDeclarationError(...) METHOD_DECLARATION       a mis-declared BaseMethod (import-time)
└─ ModelNotBound(model)        MODEL_NOT_BOUND          bound op on an unbound DTO
```

Branch on the type and read the args:

```python
from pylzt import NotFound, RateLimited, AuthFailed, LztError, ItemId


try:
    lot = await client.market.get_lot(ItemId(123))
except NotFound as e:
    print("gone:", e.item_id)
except RateLimited as e:
    print("slow down, retry after", e.retry_after)
except AuthFailed as e:
    print("dead token:", e.token_id)
except LztError as e:
    print("other upstream signal:", e.code)  # ErrorCode enum, stable classification
```

Catch `LztError` for a catch-all; catch a subclass for a specific case. `e.code` is an
`ErrorCode` (a `StrEnum`) — use it for logging/metrics dimensions.

## Retries are automatic

`Client` runs every request through its `BaseRetryPolicy` (default `ExponentialBackoff`)
**before** the error reaches you — you only see an exception once retries are exhausted or
the error is terminal.

- **Retried** with backoff + jitter: `RateLimited` (respects `Retry-After`),
  `TransportError` (5xx), `RetryableUpstream`, `ProxyChallenge` (after rotating the proxy).
- **Terminal** — never retried: `AuthFailed`, `Forbidden`, `NotFound`, `BadRequest`.

Change the policy per-client (see [Configuration](configuration.en.md)):

```python
from pylzt import BaseRetryPolicy, Client


class FixedTwice(BaseRetryPolicy):
    def next_delay(self, attempt: int, exc) -> float | None:
        return 1.0 if attempt < 2 else None


client = Client(tokens=[...], retry=FixedTwice())
```

## Response narrowing — the `check()` registry (what counts as an error)

"Is this response an error?" is decided by `LztError.match(status, headers, body)`:
every wire-facing `LztError` subclass self-registers (via `__init_subclass__`, opting in
with `__wire__ = True`) and owns a `check()` classmethod; `match()` walks the registry in
`__priority__` order (lower first) and returns the first hit, or `None` on success.
`HttpxSession` calls `match()` itself — there is nothing to wire up.

| status | error |
|---|---|
| `< 400` (unless a body marker matches, see below) | *(ok)* |
| `429` | `RateLimited` (reads `Retry-After`) |
| `401` | `AuthFailed` |
| `403` | `Forbidden` |
| `404` | `NotFound` |
| `>= 500` | `TransportError` |
| other `4xx` | `BadRequest` |

Some APIs return `200 OK` with an error in the body — `RetryableUpstream`,
`ProxyChallenge` and `CaptchaRequired` all check the body's error text regardless of
status, and run ahead of the status-bucket checks (lower `__priority__`).

Extend the registry by subclassing `LztError` — the subclass registers itself at
definition time, no wiring, no chaining:

```python
from collections.abc import Mapping
from typing import Any

from pylzt import LztError, ErrorCode


class PurchaseRejected(LztError):
    __wire__ = True
    __priority__ = 15  # ahead of the built-in status buckets, after RateLimited

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(ErrorCode.BAD_REQUEST)

    @classmethod
    def check(cls, status: int, headers: Mapping[str, str], body: Mapping[str, Any]) -> "LztError | None":
        if body.get("status") == "error":
            return cls(body.get("reason", ""))
        return None
```

Lower `__priority__` runs first; pick a number relative to the built-in table above
(`RateLimited=10` … `TransportError=90`) depending on how specific your check is.

## Optional-dependency errors

`HttpxSession` imports `httpx` lazily; if it's somehow missing from the environment,
constructing a transport raises `DependencyMissing("httpx")` — a typed, actionable error,
not a bare `ImportError` at import time. `httpx` is a core dependency of `pylzt`, so
this only fires in a broken install.

## User-facing surface

`LztError` args are for **you** (the integrator). If you surface failures to end users, map
at your boundary to a friendly message + a stable code (`e.code`) and keep the structured
detail server-side — don't leak `token_id` / infra detail outward.
