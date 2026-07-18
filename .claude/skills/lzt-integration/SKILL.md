---
name: lzt-integration
description: >
  Integration guide for AI agents (and humans) building ON TOP of this repo — the
  lzt-core typed async SDK over the lzt.market catalog API, and the event_engine
  poll→durable-log→catch-up-bus that turns the poll-only marketplace into an
  event stream. Use this whenever someone wants to USE/EMBED the library rather
  than change its internals: read the catalog with the `Client`, page lots,
  handle SDK errors, subscribe to domain events (NEW_LOT / PRICE_DROPPED / …),
  embed the engine in-process with a `BaseModule` plugin + `bus.register()`,
  receive events over a signed webhook, verify the HMAC signature, run the daemon,
  or replay/redrive/prune a consumer cursor. Triggers — RU: "как использовать
  lzt-core", "как заюзать либу", "интегрировать движок в свой проект", "создать
  подписку", "подписаться на события", "получать вебхуки от lzt", "написать плагин
  для event_engine", "BaseModule", "какие есть методы у Client", "как пагинировать
  лоты"; EN: "use lzt-core", "integrate the event engine", "create a subscription",
  "subscribe to lot events", "receive lzt webhooks", "verify webhook signature",
  "write an event_engine plugin", "embed the bus", "Client methods", "paginate lots".
  NOT for changing library internals / adding a new ABC backend (that's a
  contributor task — read CONTRIBUTING.md + the relevant `_MODULE.md`).
---

# lzt-integration — how to build on lzt-core + event_engine

You are integrating this repo as a **dependency**, not editing its internals. The repo ships two
packages; pick the surface you need:

| You want to… | Use | Section |
|---|---|---|
| Read the lzt.market catalog (lots, categories) from Python | `lzt_core.Client` | §1 |
| React to marketplace changes **inside your own Python process** | `event_engine` + a `BaseModule` plugin on the bus | §2 |
| Receive marketplace events **in a separate service / language** | the engine's signed **webhook** delivery + Management API | §3 |
| Run / operate the engine daemon | `python -m event_engine` + `scripts/` | §4 |

**Hard rules of this codebase** (your integration code should match them): everything is typed
(`mypy --strict`, `py.typed` shipped), money is `Decimal`, datetimes are UTC-aware, errors are a
typed hierarchy carrying args (never bare strings), and the API is **POST/GET only** (no
PUT/PATCH/DELETE — enforced by CI). Reuse the project's enums (`EventType`, `Category`,
`Currency`, `RateClass`) — never pass raw string literals where an enum exists.

---

## §1 — Reading the catalog with `lzt_core.Client`

Install the SDK surface and construct a client with one or more lzt.market API tokens. The client
funnels every call through a **multi-token pool + per-token rate limiter**, so a fleet pages the
whole catalog without tripping a `429`. Zero I/O happens at import or construction — all I/O is on
an awaited method.

```python
import asyncio
from pylzt.client import Client
from pylzt.models.lot import LotFilter
from pylzt.types import Category, ItemId


async def main() -> None:
    # tokens: a JSON-array of lzt.market API keys. More tokens = more headroom.
    async with Client(tokens=["<lzt-token>"]) as client:  # async CM → auto aclose()
        # Single lot
        lot = await client.market.get_lot(ItemId(12345678))

        # Batched reads coalesce into ONE rate-limited /batch request:
        lots = await client.market.get_lots_batch([ItemId(1), ItemId(2), ItemId(3)])

        # Categories
        cats = await client.market.list_categories()  # list[Category]

        # Paginated search — never touch page/has_more yourself (Law 22):
        page = client.market.list_lots(LotFilter(category=Category.STEAM))  # -> Paginator[Lot]
        first = await page.first_page()  # smoke/demo
        everything = await page.collect()  # drain (optionally capped)


asyncio.run(main())
```

### Client surface (the whole public read API)

| Method | Returns | Note |
|---|---|---|
| `list_lots(filter: LotFilter)` | `Paginator[Lot]` | lazy; iterate via `first_page()` / `collect()` |
| `await get_lot(item_id: ItemId)` | `Lot` | raises `NotFound` if absent |
| `await get_lots_batch(item_ids)` | `list[Lot]` | coalesced into one `/batch` POST = one rate lease |
| `await list_categories()` | `list[Category]` | |
| `await category_params(category)` | filter params for that category | |
| `await category_games(category)` | games under that category | |
| `await aclose()` | — | idempotent; or use `async with` |

### Constructor — everything is injectable (DI with working defaults)

```python
Client(
    tokens=["..."],            # or pass an explicit token_pool=
    transport=...,             # BaseTransport — defaults to the lolzteam backend (extra: lolzteam)
    token_pool=..., proxy_source=..., retry=..., metrics=..., clock=...,
    config=ClientConfig(...),  # frozen knobs: base_url, *_per_min, request_timeout, per_page, batch_*
)
```

You normally pass only `tokens` (and maybe `config`). The other seams exist for testing and for
swapping the backend; you don't need them to consume the catalog.

### Escape hatch — call any endpoint

The typed methods wrap the common reads; for anything the SDK doesn't wrap yet, `Client.request`
sends an arbitrary call **through the same rail** (token pool → rate limit → retry → typed error)
and returns the decoded `Response`:

```python
from pylzt.types import RateClass


resp = await client.request("GET", "/some/path", query={"page": "2"})
resp = await client.request("POST", "/x", json_body={"k": "v"}, rate_class=RateClass.GENERAL)
print(resp.status, resp.body)
```

The raw third-party `LOLZTEAM` client is deliberately not exposed — calling it directly would bypass
the pool and break the per-token rate-limit accounting. Use `request` to stay inside the rail.

### Errors — branch on type, not on text

Every upstream signal is one `LztError` subclass (root: `lzt_core.errors.LztError`, classified by
`ErrorCode` StrEnum). Catch the specific one:

```python
from pylzt.errors import NotFound, RateLimited, Forbidden, AuthFailed, LztError


try:
    lot = await client.market.get_lot(ItemId(1))
except NotFound:
    ...  # item gone / never existed
except RateLimited as e:
    await asyncio.sleep(e.retry_after or 1.0)
except LztError as e:
    log.warning("upstream", code=e.code)  # code is an ErrorCode
```

Subclasses carry **typed args** (`NotFound(item_id)`, `AuthFailed(token_id)`, `Forbidden(scope)`,
`RateLimited(retry_after)`, `TransportError(status)`). The pool already retries transient errors
internally per the injected `BaseRetryPolicy`; what reaches you is terminal.

---

## §2 — Embedding the engine in-process (a `BaseModule` plugin)

This is the **in-process subscription** path: run the engine inside your own Python app and react
to domain events with a plugin. The bus is a **catch-up** dispatcher (pull-forward by cursor, not
fire-and-forget): each module has a stable `name` that is its cursor key, so it resumes exactly
where it left off after a restart — zero-loss, replayable. Raising inside `handle` parks the event
in the DLQ after `LZT_MAX_HANDLE_ATTEMPTS`, so one poison event can't head-of-line-block you.

### The plugin contract (`event_engine.plugins.module`)

```python
class BaseModule(ABC):
    name: str                                          # stable cursor key — pick once, never change
    subscriptions: list[BaseSubscription[DomainEvent]] # what this module wants
    def wants(self, event) -> bool: ...                # default = any subscription matches
    @abstractmethod
    async def handle(self, event: DomainEvent) -> None: ...

@dataclass(frozen=True, slots=True)
class BaseSubscription[E: DomainEvent]:
    event_types: frozenset[EventType]                  # which types
    filters: Mapping[str, str] = {}                    # payload equality filters (str-compared)
    event_cls: type[E] | None = None                   # optional: pin a concrete class → narrows type
```

### Write a plugin

```python
from lzt_eventus.plugins.module import BaseModule, BaseSubscription
from lzt_eventus.events.base import DomainEvent, EventType
from lzt_eventus.events.lot import PriceDropped


class DealWatcher(BaseModule):
    name = "deal-watcher"  # ← its cursor identity
    subscriptions = [
        BaseSubscription(
            event_types=frozenset({EventType.PRICE_DROPPED}),
            event_cls=PriceDropped,  # lets handle() rely on the narrowed type
            # filters={"category": "steam"},           # optional payload filter
        )
    ]

    async def handle(self, event: DomainEvent) -> None:
        assert isinstance(event, PriceDropped)  # guaranteed by event_cls
        if event.new_price < event.old_price * Decimal("0.85"):
            await self.notify_my_system(event.lot, event.new_price)
```

### Register it and run

```python
from lzt_eventus.config import EngineConfig
from lzt_eventus.engine import EventEngine


config = EngineConfig()  # reads LZT_* env (see §4)

# In-memory stores — great for tests / a single-process embed with no Postgres:
engine = EventEngine.build_memory(config)
engine.bus().register(DealWatcher())
await engine.run()  # supervises pollers + bus; runs until stop

# Or the durable build (Postgres-backed log/cursor/DLQ):
engine, sessionmaker = EventEngine.build(config)
engine.bus().register(DealWatcher())
await engine.run()
```

`engine.bus()` → `CatchUpBus` (`register(module)`, `notify()`, `pump_once()`, `run(stop)`).
`engine.stores()` → `Stores(log, last_seen, cursor, dlq)`. `engine.drain_once()` does one poll +
one bus pump (handy in tests). `LoggingModule` in `event_engine/plugins/` is a minimal working
reference plugin — copy its shape.

### Decorator registration (`EventRouter`)

Prefer decorators to a subclass? `EventRouter` *is* a `BaseModule` (one cursor) whose handlers are
bound by `@router.on(...)`. Register it like any module:

```python
from lzt_eventus.events.base import DomainEvent, EventType
from lzt_eventus.events.lot import PriceDropped
from lzt_eventus.plugins.router import EventRouter


router = EventRouter("price-bot")  # name = cursor identity


@router.on(EventType.NEW_LOT)
async def on_new(event: DomainEvent) -> None: ...


@router.on(EventType.PRICE_DROPPED, event_cls=PriceDropped)  # one handler, many types allowed
async def on_drop(event: DomainEvent) -> None:
    assert isinstance(event, PriceDropped)  # narrowed by event_cls


engine.add_module(router)  # or modules=[router] at build
```

A raising handler propagates → the bus retries the whole router and parks past `max_handle_attempts`.

---

## §3 — Receiving events over a signed webhook (out-of-process / any language)

If your consumer is a separate service (or not Python), subscribe via the **Management API** and
receive **HMAC-signed POSTs**. The engine renders each subscription as a cursor-bearing sink over
the durable log, so a webhook consumer also gets catch-up + retry + DLQ.

### 1. Create the subscription (admin-key guarded)

```bash
curl -X POST http://<engine-host>:9189/subscriptions/create \
  -H "X-API-Key: $LZT_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "transport": "webhook",
        "endpoint": "https://your-service.example/lzt-hook",
        "event_types": ["new_lot", "price_dropped"],
        "backfill": false
      }'
# → 200 {"data": {"secret": "<per-subscription signing secret>", ...}}
```

Store the returned `secret` — it signs every delivery to your endpoint. `backfill: true` replays
historical events from the start of the log; `false` starts at "now". `event_types` are the
lowercase `EventType` slugs (`new_lot`, `price_dropped`, `lot_updated`, `lot_disappeared`, …).
The API is **POST/GET only**; there is no DELETE — manage lifecycle via the provided endpoints.

### 2. Verify the signature on your receiver (always — never trust an unsigned body)

Each delivery carries these headers (`event_engine/delivery/signing.py`):

| Header | Meaning |
|---|---|
| `X-LZT-Signature` | `sha256=<hex HMAC-SHA256 of the raw body under your secret>` |
| `X-LZT-Event-Id` | stable event UUID (use for idempotency) |
| `X-LZT-Event-Type` | the `EventType` slug |
| `Idempotency-Key` | dedupe key — process each at-most-once |

If your receiver is Python, reuse the shipped verifier; otherwise replicate the HMAC:

```python
from lzt_eventus.delivery.signing import verify_webhook


async def handle_hook(request) -> Response:
    body = await request.body()  # the RAW bytes, pre-parse
    sig = request.headers.get("X-LZT-Signature")
    if not verify_webhook(secret=MY_SECRET, body=body, presented=sig):  # constant-time check
        return Response(status_code=401)
    if already_processed(request.headers["Idempotency-Key"]):  # at-least-once → dedupe
        return Response(status_code=200)
    event = json.loads(body)
    ...  # your logic
    return Response(status_code=200)  # 2xx = ack; non-2xx → retried → DLQ
```

Return **2xx fast** to ack. `5xx` and transient `4xx` (timeout / rate-limit) are retried with
backoff; after `LZT_MAX_HANDLE_ATTEMPTS` the event is dead-lettered (recover with `scripts/redrive.sh`).

---

## §4 — Running & operating the engine

```bash
# Full self-hosted stack (Postgres 16 + Redis 7 + engine) via Docker Compose:
scripts/install.sh          # bootstrap: deps → .env → db → migrate → daemon (health-gated)
# Or without Docker:
uv sync --extra engine --extra lolzteam
uv run python -m event_engine run            # --dry-run to poll+diff without writing
```

Config is env-driven via `EngineConfig` (`LZT_` prefix; full list in `.env.example` / README).
The two **required** vars: `LZT_TOKENS` (JSON array of API tokens) and `LZT_ADMIN_API_KEY`
(`openssl rand -hex 32`). Useful knobs: `LZT_CATEGORIES`, `LZT_*_CADENCE`, `LZT_PER_PAGE`,
`LZT_MAX_HANDLE_ATTEMPTS`, `LZT_RETENTION_MONTHS`, `LZT_DEAL_THRESHOLD`,
`LZT_HEALTH_HOST/PORT` (daemon `/healthz`, `/readyz`, `/metrics`).

Daemon subcommands & ops scripts:

| Need | Command |
|---|---|
| Run the daemon | `python -m event_engine run` |
| Rewind a consumer to backfill | `python -m event_engine replay --consumer X --from-seq N` (or `scripts/replay.sh`) |
| Re-inject dead-lettered events after a fix | `python -m event_engine redrive --consumer X` (or `scripts/redrive.sh`) |
| Drop event-log rows below the watermark | `python -m event_engine prune` (or `scripts/prune.sh`) |
| Health / logs / lifecycle | `scripts/status.sh`, `scripts/logs.sh --follow`, `scripts/update.sh`, `scripts/stop.sh` |

---

## Event taxonomy (what you can subscribe to)

`EventType` (StrEnum, 37 members) is the closed catalog. The **catalog family** (the ones emitted
today) lives in `event_engine/events/lot.py` + `marker.py`:

| `EventType` | Event class | Key payload fields |
|---|---|---|
| `NEW_LOT` | `NewLotAppeared` | `lot: Lot` |
| `PRICE_DROPPED` | `PriceDropped` | `old_price: Decimal`, `new_price: Decimal`, `lot: Lot` |
| `LOT_UPDATED` | `LotUpdated` | `lot: Lot`, `changed: frozenset[str]` |
| `LOT_DISAPPEARED` | `LotDisappeared` | `reason: DisappearReason`, `confidence: Confidence` |
| `SNAPSHOT_INITIALIZED` | `SnapshotInitialized` | `category: Category`, `lot_count: int` (cold-start marker, not a per-lot flood) |

Every event subclasses `DomainEvent` (carries `event_id: UUID`, `aggregate_id`, `occurred_at`
UTC, `content_hash`, `schema_version`, `seq`, `payload`). Event ids are **deterministic** — the
same logical event always hashes to the same UUID (`make_event_id`), which is what makes the
stream safely replayable and dedupable. The remaining `EventType` members (balance / purchase /
guarantee families) are defined for forward-compat; check `events/_MODULE_AUTO.md` for current
emit status before subscribing to a non-catalog type.

---

## Where to look (doc-first, per the repo's own rule)

1. `README.md` — quickstart, run modes, full `LZT_` config table.
2. `ROADMAP.md` + `docs/architecture.md` — scope, non-goals, architecture.
3. `src/<pkg>/_MODULE_AUTO.md` — the generated public surface of every package (read this before source).
4. `src/lzt_eventus/consumers/` — the `BaseConsumer` contract + `LoggingConsumer` reference plugin.
5. `CONTRIBUTING.md` — only if you're changing the library itself, not just using it.

> If you're **extending the core** (new event type, new route, new event source, new store /
> transport backend) by subclassing + overriding rather than consuming it as-is, use the companion
> **`lzt-extending`** skill — it maps every ABC seam, says which are constructor-injectable, and
> ships a worked example per axis.
