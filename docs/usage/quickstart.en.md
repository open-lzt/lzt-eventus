# Quickstart

<p align="right"><b>English</b> · <a href="quickstart.md">Русский</a></p>

## Install

```bash
pip install pylzt
```

`import pylzt` alone opens no socket — `httpx` (the SDK's one transport, `HttpxSession`)
is imported lazily only when a `Client` actually sends a request.

## Build a client

The client is an async context manager — always use it as one, so connections and the
token pool are released on exit (Law 24).

```python
import asyncio
from pylzt import Client, LotFilter, Category


async def main() -> None:
    async with Client(tokens=["<your-lzt-market-token>"]) as client:
        # newest 20 Steam lots
        lots = await client.market.list_lots(
            LotFilter(category=Category.STEAM)
        ).collect(limit=20)
        for lot in lots:
            print(lot.item_id, lot.price, lot.currency, lot.title)


asyncio.run(main())
```

`tokens=[...]` accepts raw token strings (wrapped into `Token`s internally) — pass more
than one and the pool rotates across them, metering each token's rate budget separately.

## Adding a request middleware

`Client(tokens=[...])` builds its own `HttpxSession` under the hood; inject your own
instance to register middlewares (logging, tracing) on the request chain (see
[Configuration](configuration.en.md)):

```python
from pylzt import Client, HttpxSession


session = HttpxSession(base_url="https://prod-api.lzt.market")
session.request_middlewares.register(MyLoggingMiddleware())
async with Client(tokens=["<token>"], transport=session) as client:
    lot = await client.market.get_lot(ItemId(123456))
```

## What the client exposes

| Call | Returns | Notes |
|---|---|---|
| `list_lots(filter, *, max_pages=None)` | `Paginator[Lot]` | `async for` or `.collect(limit=)` |
| `get_lot(item_id)` | `Lot` | bound — `await lot.refresh()` re-fetches |
| `get_lots_batch(item_ids)` | `list[Lot]` | `/batch` requests chunked to the server's 10-job cap, input order |
| `list_categories()` | `list[Category]` | active market categories |
| `category_params(category)` | `FilterSchema` | filter schema, cached (TTL) |
| `category_games(category)` | `list[CategoryGame]` | games in a category |
| `execute(method)` | `T` | run a custom `BaseMethod[T]` |
| `request(method, path, ...)` | `Response` | escape hatch for un-wrapped endpoints |

This is a **read-only** surface — there are no buy/publish methods (that lives in a
downstream module built on these seams). Next: [Reading the catalog](catalog.en.md).
