# Extending pylzt

<p align="right"><b>English</b> · <a href="extending.md">Русский</a></p>

Everything variable in the SDK is a seam: subclass a base from the **public surface**
(`pylzt.__all__`) and pass it in — never edit the library. This is the contract a
downstream module (e.g. `autobuy`) builds on. For the AI-agent deep guide see the
`lzt-extending` skill; this page is the human-facing map.

## Seam map

| You vary… | Subclass | Inject via | Default |
|---|---|---|---|
| An API operation | `BaseMethod[T]` | `client.execute(MyMethod(...))` | the catalog methods |
| The upstream backend | `BaseTransport` | `Client(transport=...)` | `HttpxSession` |
| Cross-cutting request policy | `BaseMiddleware` | `session.request_middlewares.register(...)` | `LoggingMiddleware` |
| "Is this response an error?" | `LztError` (`__wire__ = True`) | subclass — self-registers | 9 built-in wire errors |
| Token selection strategy | `BaseTokenSelector` | `RoundRobinTokenPool(tokens, selector=...)` | `RoundRobinSelector` |
| The whole token pool | `BaseTokenPool` | `Client(token_pool=...)` | `RoundRobinTokenPool` |
| Proxy supply | `BaseProxySource` | `Client(proxy_source=...)` | none (`NullProxyPool`) |
| Retry policy | `BaseRetryPolicy` | `Client(retry=...)` | `ExponentialBackoff` |
| Read-through cache backend | `BaseCache[T]` | `Client(category_cache=...)` | `MemoryCache` |
| Metrics sink | `BaseMetrics` | `Client(metrics=...)` | `NullMetrics` |
| Time source (tests) | `Clock` | `Client(clock=...)` | `RealClock` |
| A DTO with bound ops | `BoundModel` | returned bound by `execute` | `Lot` |

## Examples

A custom operation (method-as-class — a frozen dataclass, no pydantic):

```python
from dataclasses import dataclass
from pylzt import BaseMethod, HttpMethod, Client


@dataclass(frozen=True, slots=True)
class GetSellerRating(BaseMethod[float]):
    __http_method__ = HttpMethod.GET
    __url__ = "/seller/{seller_id}/rating"
    __path_fields__ = frozenset({"seller_id"})
    seller_id: int

    def parse_response(self, response):
        return float(response.body.get("rating", 0.0))


rating = await client.execute(GetSellerRating(seller_id=42))
```

A custom token-selection strategy (the pool still enforces rate budget):

```python
from pylzt import BaseTokenSelector, RoundRobinSelector  # subclass either
from pylzt.token_pool.round_robin import RoundRobinTokenPool


pool = RoundRobinTokenPool(tokens, selector=MyWeightedSelector())
client = Client(token_pool=pool)
```

A Redis cache backend, shared across processes:

```python
from pylzt import BaseCache, Client


class RedisCache(BaseCache[dict]):
    async def get(self, key): ...

    async def set(self, key, value, *, ttl): ...


client = Client(tokens=[...], category_cache=RedisCache())
```

## Rules your extension keeps

Typed everything (`mypy --strict`); `Decimal` money; UTC datetimes; a typed error subclass
of `LztError` carrying **args** (not a formatted string); for a new store ABC, ship a
`Memory*` default + a contract test (`tests/pylzt/contracts/`) so your backend proves it
behaves identically. Never leak a third-party type (`httpx.*`) across a public signature.
