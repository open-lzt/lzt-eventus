# Reading the catalog

<p align="right"><b>English</b> · <a href="catalog.md">Русский</a></p>

Every read returns a typed, frozen DTO — the raw wire dict never escapes the parser.
Money is `Decimal`, datetimes are UTC-aware, ids are opaque (`ItemId`).

## Filtering: `LotFilter`

```python
from decimal import Decimal
from pylzt import LotFilter, Category, OrderBy


flt = LotFilter(
    category=Category.STEAM,  # path segment, not a query param
    pmin=Decimal("5"),  # min price
    pmax=Decimal("50"),  # max price
    title="cs2",  # title contains
    game=("730",),  # app ids (tuple → repeated query param)
    order_by=OrderBy.NEWEST,  # PRICE_ASC / PRICE_DESC / NEWEST / OLDEST
)
```

Every field is optional; omit what you don't filter on. `category=None` lists across all
categories.

## Paging: `list_lots` → `Paginator[Lot]`

Pagination is an async iterator — you never touch `page=` / `has_more` (Law 22).

```python
async with Client(tokens=["<token>"]) as client:
    # stream every matching lot, page by page, lazily
    async for lot in client.market.list_lots(flt):
        handle(lot)

    # or drain into a list, capped
    first_20 = await client.market.list_lots(flt).collect(limit=20)

    # or just the first page
    page = await client.market.list_lots(flt).first_page()
```

Cap total pages with `max_pages` to bound a broad sweep:

```python
client.market.list_lots(flt, max_pages=5)   # at most 5 `search`-class requests
```

## A single lot: `get_lot`

```python
from pylzt import ItemId


lot = await client.market.get_lot(ItemId(123456))
print(lot.price, lot.currency, lot.item_state, lot.seller_id)
```

### Bound `refresh()`

A `Lot` returned by the client carries the client that produced it, so it can re-fetch
itself (aiogram-style bound method):

```python
lot = await client.market.get_lot(ItemId(123456))
# …later, after the market may have moved…
fresh = await lot.refresh()          # returns a new, bound Lot
```

A `Lot` you built or parsed yourself is **unbound** — calling `refresh()` on it raises
`ModelNotBound` (fail loud, never a silent no-op).

## Many at once: `get_lots_batch`

N ids are chunked into `ceil(N / 10)` concurrent `/batch` requests (the server caps a
single request at 10 jobs). Results come back in input order; ids absent from the
response are silently skipped.

```python
lots = await client.market.get_lots_batch([ItemId(1), ItemId(2), ItemId(3)])
# len(lots) may be < 3 if some ids no longer exist
```

Need a per-item `NotFound` instead of a silent skip? Use the request-coalescing
`BatchExecutor.submit(item_id)` primitive (`pylzt.lib.batch`).

## The `Lot` shape

| Field | Type | Notes |
|---|---|---|
| `item_id` | `ItemId` | opaque int id |
| `category` | `Category` | enum; unknown slugs → `OTHER` |
| `price` | `Decimal` | never `float` |
| `currency` | `Currency` | travels with the amount |
| `title` | `str` | |
| `seller_id` | `SellerId` | |
| `published_at` | `datetime` | UTC-aware |
| `item_state` | `str` | raw upstream state (UNVERIFIED vocabulary) |
| `item_origin` | `ItemOrigin` | how the account was obtained |
| `guarantee` | `str` | |
| `nsb` | `bool` | |
| `content_hash` | `str` | digest over **price-relevant** fields only |
| `attributes` | `Mapping[str, str]` | description / information |

`content_hash` is stable across a pure metadata refresh (view count, bumped date) — a
dedup/diff consumer keys on it, so a cosmetic change doesn't look like a changed listing.

## Categories

```python
cats = await client.market.list_categories()               # list[Category]
schema = await client.category_params(Category.STEAM)  # FilterSchema (cached, TTL)
games = await client.category_games(Category.STEAM)    # list[CategoryGame]
```

`category_params` is read-through a cache (default in-memory, TTL
`config.category_params_ttl`); swap the backend via `Client(category_cache=...)` — see
[Configuration](configuration.en.md). `FilterSchema` / `CategoryGame` are named boundary
types over an as-yet-UNVERIFIED upstream shape.

## Custom endpoints

Two escape hatches for endpoints the SDK doesn't wrap:

```python
# 1. Raw request through the rate-limited rail (you lose the DTO, keep the pool/retry):
resp = await client.request("GET", "/some/path", query={"x": 1})   # -> Response

# 2. A typed method-as-class (you keep the DTO). See docs/extending.md.
result = await client.execute(MyMethod(...))
```
