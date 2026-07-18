# Configuration & dependency injection

<p align="right"><b>English</b> · <a href="configuration.md">Русский</a></p>

Everything pluggable is a constructor argument with a working default — nothing is
hardcoded, there are no globals. Pass only what you want to change.

## `Client` constructor

```python
Client(
    tokens=None,            # Sequence[str | Token] — raw tokens, wrapped into the pool
    *,
    transport=None,         # BaseTransport   — default: HttpxSession(config.base_url)
    token_pool=None,        # BaseTokenPool   — default: RoundRobinTokenPool(tokens)
    proxy_source=None,      # BaseProxySource — default: none (NullProxyPool)
    retry=None,             # BaseRetryPolicy — default: ExponentialBackoff
    metrics=None,           # BaseMetrics     — default: NullMetrics
    clock=None,             # Clock           — default: RealClock
    category_cache=None,    # BaseCache       — default: MemoryCache
    config=None,            # ClientConfig    — default: ClientConfig()
)
```

Give either `tokens=` (the pool is built for you) **or** a fully-built `token_pool=`.

## `ClientConfig`

A single frozen, typed config object (Law 20) — no kwargs soup:

```python
from pylzt import ClientConfig, Client


config = ClientConfig(
    base_url="https://prod-api.lzt.market",
    general_per_min=20,  # `general`-class budget per token
    search_per_min=10,  # `search`-class budget per token (listing pages)
    request_timeout=30.0,
    per_page=50,
    batch_size=50,  # /batch job cap
    batch_linger=0.05,  # batch-coalescing window (seconds)
    category_params_ttl=3600.,  # category_params cache TTL
)
client = Client(tokens=["<token>"], config=config)
```

## Rate limiting (why it's automatic)

Each request declares a `RateClass` (`GENERAL` or `SEARCH`); the token pool blocks until a
token has budget in that class, so **you never sleep or count requests yourself**. Budgets
are **per token** — add more tokens to scale throughput linearly. Listing pages are
`search`-class; single lots, batches and categories are `general`-class.

## Swapping backends

### Cache (share across processes)

```python
from pylzt import BaseCache, Client


class RedisCache(BaseCache[dict]):
    async def get(self, key): ...

    async def set(self, key, value, *, ttl): ...


client = Client(tokens=["<token>"], category_cache=RedisCache())
```

### Proxies

Supply exit IPs by implementing `BaseProxySource`; the pool binds a **sticky** proxy per
token and feeds success/ban outcomes back to per-proxy health.

```python
client = Client(tokens=[...], proxy_source=MyProxySource())
```

### Retry policy

```python
from pylzt import BaseRetryPolicy, ExponentialBackoff


class NoRetry(BaseRetryPolicy):
    def next_delay(self, attempt: int, exc) -> float | None:
        return None  # give up immediately


client = Client(tokens=[...], retry=NoRetry())  # default: ExponentialBackoff
```

`ExponentialBackoff` honours `Retry-After` on `429`, jitters, and treats
`AuthFailed / Forbidden / NotFound / BadRequest` as terminal (no retry).

### Metrics & observability

The client emits counters through an injected `BaseMetrics`; the default is a no-op. Wire
Prometheus / OTel without the SDK importing either:

```python
from pylzt import BaseMetrics, Client


class PromMetrics(BaseMetrics):
    def incr(self, name, value=1, **labels): ...

    def gauge(self, name, value, **labels): ...

    def observe(self, name, value, **labels): ...


client = Client(tokens=[...], metrics=PromMetrics())
```

### Token selection strategy

The default pool rotates fairly across the fleet. Reprioritise (weighted, health-aware,
LRU) by injecting a `BaseTokenSelector` — the pool still enforces each token's rate budget,
the selector only decides the **order** tokens are tried:

```python
from pylzt import BaseTokenSelector, RoundRobinSelector
from pylzt.token_pool.round_robin import RoundRobinTokenPool


pool = RoundRobinTokenPool(tokens, selector=MyWeightedSelector())
client = Client(token_pool=pool)
```

### Transport + middleware

`HttpxSession` is the SDK's only transport — an httpx-backed `BaseTransport` with a
request middleware chain. Response narrowing (status/body → typed `LztError`) is the
self-registering `LztError.check()` registry, not a transport option — extend it by
subclassing `LztError` (full detail in [Error handling](errors.en.md) and
[Extending](../extending.en.md)):

```python
from pylzt import HttpxSession, Client


session = HttpxSession(base_url="https://prod-api.lzt.market")
session.request_middlewares.register(MyLoggingMiddleware())
client = Client(tokens=[...], transport=session)
```

## Testing with a fake clock

`clock=FakeClock()` makes rate-limit and cache-TTL logic deterministic in tests — advance
time by hand instead of sleeping:

```python
from pylzt import Client, FakeClock


clock = FakeClock()
client = Client(tokens=[...], clock=clock)
clock.advance(60.0)  # a minute of budget refills, no real wait
```
