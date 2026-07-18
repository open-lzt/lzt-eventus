---
name: lzt-extending
description: >
  Extension / plugin-authoring guide for the event_engine core — how to add new
  behaviour by SUBCLASSING the project's ABCs and INJECTING via the constructor,
  never by editing library source (open-closed, Law 5). Covers: a new domain event
  type (subclass `DomainEvent` + `EventType` member — the codec is registry-free,
  so it just works), a new event SOURCE / poller (`BasePoller`), a new HTTP route
  (`app.include_router` over `build_app`), a new store backend (`BaseEventLog` /
  `BaseCursorStore` / `BaseLastSeenStore` / `DeadLetterStore` / `BaseSubscriptionRepo`),
  a new webhook transport (`BaseWebhookTransport`), a new SDK transport
  (`BaseTransport`), and a new in-process subscriber (`BaseModule`). Explains which
  seams are constructor-injectable today vs which currently need a self-composed
  runner, and the bus CONCURRENCY contract a module/source author builds against
  (one worker per module → sequential within a module, concurrent across modules;
  `notify` vs `notify_membership`; the `bus_max_concurrent_modules` bulkhead).
  Triggers — RU: "как добавить новое событие", "новый тип события",
  "добавить роут/эндпоинт", "новый источник событий", "свой поллер", "свой бэкенд
  хранилища", "расширить ядро", "плагинирование", "наследники и переопределения",
  "не меняя код либы", "свой транспорт/сток", "как шина обрабатывает события",
  "конкурентность шины", "параллельная обработка событий", "порядок событий в модуле";
  EN: "add a new event type", "new
  EventType", "add a route/endpoint", "new event source", "custom poller", "custom
  store backend", "extend the engine", "plugin the core", "subclass and override",
  "without editing the library", "new transport/sink", "bus concurrency model",
  "are handlers called concurrently", "event ordering guarantee". NOT for plain USAGE
  (reading the catalog / creating a subscription as a consumer) — that's the
  `lzt-integration` skill.
---

# lzt-extending — grow the core by subclass + inject, never by editing it

The engine is **open-closed** (Law 5): new behaviour is a new subclass passed in, proven by
`event_engine/plugins/LoggingModule` ("a real subscriber, zero engine edits"). Before writing any
extension, find the ABC for the axis you're varying, subclass it, and hand it to the constructor.
Editing library source to add your case means the seam was missed.

**The golden rules your extension must keep** (the core enforces them on itself): typed everything
(`mypy --strict`, `py.typed`), `Decimal` money, UTC-aware datetimes, a typed error subclass
carrying **args** (never a formatted string), a `Memory*` default impl + a contract test for every
new store ABC, **emit events — don't call transports** from business code, and the API stays
**POST/GET only**.

## Seam map — what to subclass, where to inject

| You want to add… | Subclass | Inject / register via | Status |
|---|---|---|---|
| In-process subscriber | `BaseModule` | `EventEngine(modules=[…])` / `bus.register()` | ✅ ctor seam |
| New domain event type | `DomainEvent` + `EventType` member | nothing — codec is registry-free | ✅ |
| Store backend (log/cursor/last_seen/dlq/sub-repo) | the store ABC | `Stores(…)` → `EventEngine(stores=…)` | ✅ ctor seam (direct ctor) |
| Webhook delivery transport | `BaseWebhookTransport` | `build_memory(webhook_transport=…)` / `WebhookDelivery(transport=…)` | ✅ ctor seam |
| SDK upstream transport | `BaseTransport` | `Client(transport=…)` | ✅ ctor seam |
| Single-owner lease / clock | `BaseLease` / `Clock` | `EventEngine(lease=…, clock=…)` | ✅ ctor seam |
| HTTP route / endpoint | a FastAPI `APIRouter` | `app.include_router(…)` over `build_app()` | ✅ compose |
| Event source / poller | `BasePoller` | `EventEngine(extra_pollers=[…])` / `build_memory(extra_pollers=…)` | ✅ ctor seam |

`EventEngine.__init__` is full constructor DI:
`EventEngine(*, client, stores, config, modules, lease=None, clock=None, delivery=None)`. The
`build_memory(...)` / `build(...)` classmethods are *convenience wirings* that pick default store
impls — to inject your own stores/transports, either pass the optional kwargs they expose or call
`EventEngine(...)` directly with your `Stores`.

---

## §1 — A new domain event type

Events are frozen `DomainEvent` subclasses with an `EVENT_TYPE: ClassVar[EventType]`. The codec is
**registry-free by design** (`codecs/json.py` — one generic JSON codec, D14/Law 0): it flattens any
event's subclass fields + payload generically, so a new event type needs **no codec registration**
as long as its fields are JSON-coercible (`to_jsonable` handles `Decimal`, datetimes, enums).

```python
# your_pkg/events.py
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from lzt_eventus.events.base import DomainEvent, EventType


@dataclass(frozen=True, slots=True)
class SellerRatingDropped(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.SELLER_RATING_DROPPED  # see note
    seller_id: int
    old_rating: float
    new_rating: float
```

`EventType` is a **closed `StrEnum`** (37 members, many reserved for forward-compat — check
`events/base.py`). If your type already exists as a reserved member, use it. If it's genuinely new,
adding a member is a one-line library change to the enum — the smallest possible edit, and the only
one the closed-catalog design intends (it keeps event ids deterministic and the wire vocabulary
single-sourced). Everything *downstream* (codec, log, bus, delivery, subscriptions) then handles
your event with zero further edits.

> Who emits it? An event enters the log from a **source** (§2) or the differ. A `BaseModule`
> *consumes*; it never emits. So a new event type travels with a new (or extended) source.

---

## §2 — A new event source / poller

A source turns some external state into persisted domain events on an adaptive cadence. Subclass
`BasePoller` and implement `poll_once()` (emit + persist, return the count); `run()` is provided.

```python
# your_pkg/source.py
import asyncio
from lzt_eventus.poller.base import BasePoller
from lzt_eventus.log.base import BaseEventLog
from lzt_eventus.bus.catchup import CatchUpBus


class SellerRatingPoller(BasePoller):
    def __init__(self, log: BaseEventLog, bus: CatchUpBus, ...) -> None:
        self._log, self._bus = log, bus

    async def poll_once(self) -> int:
        events = await self._scan()  # build SellerRatingDropped(...) instances
        for e in events:
            await self._log.append(e)  # durable, deterministic id → replayable/dedup
        if events:
            self._bus.notify()  # wake the catch-up pump
        return len(events)
```

Inject it through the **`extra_pollers`** ctor seam — your poller is supervised under the same
`TaskGroup` as the built-in `CategoryPoller`s and pumps events into the same log/bus/cursor/DLQ:

```python
spy = SellerRatingPoller(log=..., bus=...)         # see §4 for how to reach stores/bus if needed
engine = EventEngine.build_memory(config, client=client, modules=[MyModule()], extra_pollers=[spy])
# or the durable build:
engine, sm = EventEngine.build(config, modules=[MyModule()], extra_pollers=[spy])
await engine.run()                                 # built-in pollers + yours + bus + delivery
```

`extra_pollers` defaults to `()`, so existing call sites are unaffected. Build your poller against
the engine's **own** `stores.log` + `bus` (constructed first if you need references) so its events
flow through the existing machinery; if you only need the durable log, the simplest pattern is to
construct stores, pass them to `EventEngine(stores=…, extra_pollers=[…])` directly, and hand the
poller the same `stores.log`.

---

## §3 — A new HTTP route / endpoint

The web app is assembled by `event_engine/web/main.py::build_app(handle: EngineHandle) -> FastAPI`.
Add endpoints by mounting your own `APIRouter` on the returned app — no edit to the lib's routers:

```python
from fastapi import APIRouter
from lzt_eventus.web.main import build_app


extra = APIRouter()


@extra.get("/seller/{seller_id}/rating")  # GET/POST only — never PUT/PATCH/DELETE (CI-enforced)
async def seller_rating(seller_id: int) -> dict[str, object]:
    ...


app = build_app(handle)
app.include_router(extra)
```

Follow the existing conventions: handlers `raise` a typed `WebError` (`web/base/errors.py`) and let
the error middleware map it to HTTP — never raise `HTTPException`, never return a raw `dict` across
the boundary (use the `web/schemas/` envelope types). DB access goes through a repo
(`web/repos/`), never an ORM call in the handler.

---

## §4 — A new store backend (durable log, cursor, DLQ, …)

Every persistence concern is an ABC with a `Memory*` default and a `Postgres*` impl — subclass for a
new backend (e.g. Redis Streams, SQLite) and pass a `Stores` bundle to the ctor:

| ABC | File | Ships |
|---|---|---|
| `BaseEventLog` | `log/base.py` | `MemoryEventLog`, `PostgresEventLog` |
| `BaseCursorStore` | `cursor/base.py` | `MemoryCursorStore`, `PostgresCursorStore` |
| `BaseLastSeenStore` | `baseline/store.py` | `MemoryLastSeenStore`, `PostgresLastSeenStore` |
| `DeadLetterStore` | `bus/dlq.py` | `MemoryDeadLetterStore`, `PostgresDeadLetterStore` |
| `BaseSubscriptionRepo` | `web/repos/subscription_repo.py` | `Postgres…` (+ memory for tests) |

```python
class RedisEventLog(BaseEventLog):
    async def append(self, event: DomainEvent) -> int: ...      # implement the ABC contract
    # … the rest of the abstract methods

stores = Stores(
    log=RedisEventLog(...),
    last_seen=MemoryLastSeenStore(),                            # mix and match
    cursor=MemoryCursorStore(),
    dlq=MemoryDeadLetterStore(),
)
engine = EventEngine(client=Client(tokens), stores=stores, config=config, modules=[MyModule()])
```

**Ship the contract proof.** For a new store, also ship a `Memory*`-style minimal impl (if one
doesn't exist) and a test that exercises the ABC contract, mirroring the existing store tests — this
proves your backend behaves identically to the Postgres/Memory ones (Law 21).

---

## §5 — A new transport (SDK upstream or webhook delivery)

Both I/O legs sit behind an ABC so `httpx`/backend types never leak (Law 10/18):

```python
# SDK upstream — swap the lzt.market backend (AS7 → own-reverse → a Go core, …)
from pylzt.transport.base import BaseTransport, Request, Response


class MyTransport(BaseTransport):
  async def send(self, req: Request) -> Response: ...  # raise a typed LztError on failure


client = Client(tokens=["…"], transport=MyTransport())

# Webhook delivery — change how outbound POSTs are made (proxy, signing relay, queue, …)
from lzt_eventus.delivery.transport import BaseWebhookTransport


class QueueingWebhookTransport(BaseWebhookTransport):
  async def post(self, url: str, body: bytes, headers) -> int: ...  # return HTTP status


engine = EventEngine.build_memory(config, client=client, modules=[…],
subscriptions = repo, webhook_transport = QueueingWebhookTransport())
```

There's also `RequestSender` (`transport/base.py`) — the internal "run one `Request` through the
rate-limited client rail" seam that `lib/` primitives (e.g. `BatchExecutor`) and test doubles depend
on instead of the concrete `Client`. Subclass it if you build your own client-rail primitive.

---

## §6 — A new in-process subscriber (`BaseModule`)

Covered in depth by the **`lzt-integration`** skill (§2 there) — the same `BaseModule` +
`BaseSubscription` contract, registered via `EventEngine(modules=[…])` or `bus.register()`. It's
listed here because it's the most common "extend the core" axis and the canonical open-closed proof.

---

## §7 — Runtime-dynamic reconfiguration (add/drop while running)

The lifecycle components — **sources (pollers)** and **subscribers (modules)** — can be added and
dropped while `run()` is live, no restart. A reactive supervisor reconciles the desired poller set
against the running tasks; each poller gets its own stop signal, so dropping one tears down just
that task gracefully (it finishes its in-flight cycle, then exits).

```python
engine = EventEngine.build(config, modules=[DealWatcher()])[0]
runner = asyncio.create_task(engine.run())

# add a source at runtime — the supervisor starts its task immediately
engine.add_poller(SellerRatingPoller(name="seller-rating", ...))
assert "seller-rating" in engine.poller_names
# drop it later — its task stops gracefully, cursor-independent
engine.remove_poller("seller-rating")

# subscribers are equally dynamic (picked up on the next bus pump)
engine.add_module(MyModule())
engine.remove_module("my-module")          # cursor stays committed → safe resume if re-added
```

API: `add_poller(BasePoller)` / `remove_poller(name)` / `poller_names`,
`add_module(BaseModule)` / `remove_module(name)` / `module_names`. Adding a duplicate poller name
raises `DuplicatePoller`; removing an absent poller/module raises `PollerNotFound` / `ModuleNotFound`.
Pollers are keyed by their `name` (built-ins use `poller:<category>`) — give a custom poller a stable,
unique `name`.

**What is NOT runtime-mutable, on purpose.** The infra singletons — `client`, `stores`, `config`,
`lease`, `clock` — are construction-time only. Hot-swapping the event log / cursor store / client
mid-pump would strand committed cursors and break idempotency (lost or double-delivered events).
To change those, do a **graceful stop → rebuild**: `engine.request_stop()`, await `run()`, then
construct a fresh engine with the new infra. This is a deliberate safety boundary, not a gap.

## §8 — How the bus runs your modules (concurrency contract)

`CatchUpBus` is **pull-forward by cursor**, not fire-and-forget pub/sub: every subscriber owns a
`BaseCursorStore` cursor, the bus pulls `log.read_after(cursor)` and replays in seq order, committing
the cursor after **every** event (handled, skipped, or parked). That's what makes it replayable,
resumable and zero-loss. When you write a `BaseModule` (§6) or a source (§2), these are the guarantees
you build against:

- **Within your module, dispatch is strictly sequential and seq-ordered.** Under `run()` each module
  gets exactly **one** supervised worker task, so your `handle()` is never called concurrently with
  itself — your module's own state needs no intra-module locking. Events arrive in ascending `seq`,
  gapless.
- **Across modules, dispatch is concurrent.** One worker per module, independent cursors → a slow
  consumer (e.g. a webhook-posting module) **never head-of-line-blocks** a fast sibling. Implication:
  your `handle()` *can* run at the same time as a **different** module's handler. Any resource you
  share across modules (a counter, an external client) needs its own synchronisation — the bus only
  serialises within one module, not across them.
- **Poison events park, they don't block.** A `handle()` that keeps raising past
  `config.max_handle_attempts` is parked in the DLQ and the cursor still advances — one bad event can't
  stall your consumer (A5/D24). Raise a typed error from `handle()`; don't swallow.
- **`pump_once()` is sequential and deterministic** — the path `drain_once()` / `--dry-run` / tests
  use. The concurrent worker model is `run()` only, so tests stay reproducible.

### Waking and reconciling — `notify()` vs `notify_membership()`

A **source** that appended events calls `bus.notify()` (§2) — it wakes **all** module workers to pump
promptly (edge-triggered: a wake racing a "0 events" read is never lost). That's different from
**membership** changes: `bus.register()` / `unregister()` (and `EventEngine.add_module` /
`remove_module`, §7) flip an internal signal and the supervisor **reconciles** live workers — spins one
up for a new module, tears one down for a removed one (its cursor stays committed for safe resume). If
you drive membership through a `module_provider` (dynamic webhook sinks) instead of `register()`, call
`bus.notify_membership()` after your set changes so the supervisor reconciles.

```python
# in your poller, after persisting events:
if events:
    self._bus.notify()                 # new events → wake workers

# adding a subscriber at runtime (supervisor starts its worker):
engine.add_module(MyModule())          # → bus.register() → reconcile
```

### Bulkhead — bounding simultaneous pumping

By default there's one worker per module with **no global cap** (each pumps freely). To bound how many
modules dispatch at once — e.g. so 50 webhook-posting modules don't open 50 sockets at once — set
`EngineConfig.bus_max_concurrent_modules` (`0` = unbounded). It's a semaphore held **per pump batch**,
not per event, so within-module ordering is untouched; it only limits cross-module parallelism.

```python
config = EngineConfig(bus_max_concurrent_modules=8)   # ≤ 8 modules pumping simultaneously
```

> Full contract + the edge-trigger / drain internals: `event_engine/bus/_MODULE.md`. **Never add
> concurrency *inside* a module's dispatch** (e.g. `asyncio.gather` over events in `handle`) — it breaks
> the seq-ordering and cursor invariant the whole design rests on.

## §9 — Custom methods + a middleware session (for modules like `autobuy`)

A consumer module that adds operations (e.g. `autobuy` buying accounts) gets two seams: define each
operation as a **method-as-class**, and run it through an **httpx session whose middleware chain you
extend** for cross-cutting concerns (error handling, logging, retries).

**Method-as-class** — aiogram-style, **dependency-free**: `BaseMethod[T]` is a frozen dataclass
(no hand-written `__init__`). A subclass declares its request fields as dataclass fields and the
endpoint as class-vars (`__http_method__`, `__url__`, `__path_fields__`, `__returning__`); the
default `build_request` formats the path from fields and routes the rest to query (GET) / JSON body
(POST). `__init_subclass__` enforces the contract at import (missing `__url__`/`__returning__`, or
`__path__` misuse → `MethodDeclarationError`). The base runs the op through any `RequestSender`
(the rate-limited rail), so it gets the token pool, retries and typed errors for free:

```python
from dataclasses import dataclass
from pylzt.methods.base import BaseMethod
from pylzt.types import HttpMethod, ItemId


@dataclass(frozen=True, slots=True)
class BuyAccount(BaseMethod[Purchase]):  # in the autobuy module, not in lzt-core
  __http_method__ = HttpMethod.POST
  __url__ = "/{item_id}/fast-buy"
  __path_fields__ = frozenset({"item_id"})
  __returning__ = Purchase.from_body  # any (body) -> Purchase parser; no pydantic needed

  item_id: ItemId


purchase = await client.execute(BuyAccount(item_id))  # runs through the rail
```

`__returning__` is the dependency-free analog of aiogram's "validate response into the return type":
a parser callable `(body) -> T` (a `@classmethod from_body`, a module-level `parse_*`, or `dict`).
**Override `parse_response`** instead when the wire shape needs real narrowing — a nested key, batch
ordering, slug filtering (see `methods/catalog.py::GetLot`/`GetLotsBatch`). Override `build_request`
when the request isn't a flat field→param map (pagination, `/batch`).

**Read-through cache is a `BaseCache` seam, not a module global** — `lib/cache.py` ships
`MemoryCache[T]` (TTL via the injected `Clock`); the client takes `category_cache=` and reads through
it for `category_params`. Inject a Redis-backed `BaseCache` subclass to share the cache across
processes — the SDK imports nothing:

```python
from pylzt.lib.cache import BaseCache, MemoryCache


class RedisCache(BaseCache[dict[str, object]]):
  async def get(self, key): ...

  async def set(self, key, value, *, ttl): ...


client = Client(tokens=[...], category_cache=RedisCache())  # default is MemoryCache
```

**Bound methods on DTOs (aiogram-style)** — a response model returned through `execute` carries the
client that produced it, so it exposes operations on itself (`lot.refresh()`) without the caller
threading the client around. Subclass `models/_bound.py::BoundModel` (a `__slots__` base, not a
dataclass field — `_client` stays out of equality/hash/repr) and add async methods that call
`self.client`; `Client.execute` binds every `BoundModel` it returns (single, list, or `Page`). A
model built/parsed standalone raises `ModelNotBound` if a bound op is called — fail loud. A future
`autobuy` adds `lot.buy()` the same way, in its own module:

```python
@dataclass(frozen=True, slots=True)
class Lot(BoundModel):
    item_id: ItemId
    # …fields…
    async def refresh(self) -> Lot:
        return await self.client.market.get_lot(self.item_id)   # re-fetch through the same rail
```

**Middleware session** — `HttpxSession` is a `BaseTransport` whose request path runs a chain of
`BaseMiddleware` (onion: each gets `(request, call_next)`). A middleware can short-circuit, retry,
enrich, or map the response to a typed error — that's where `autobuy` plugs its error handling.
Middlewares register **aiogram-style** on `session.request_middlewares`, which is itself a decorator
(first registered = outermost; the manager dedups by class):

```python
from pylzt.transport.middleware import BaseMiddleware, Next, LoggingMiddleware
from pylzt.transport.session import HttpxSession
from pylzt.transport.base import Request, Response


session = HttpxSession(base_url="https://api.lzt.market")


@session.request_middlewares  # decorate a subclass …
class RaiseOnPurchaseError(BaseMiddleware):
  async def __call__(self, request: Request, call_next: Next) -> Response:
    resp = await call_next(request)
    if resp.body.get("status") == "error":
      raise PurchaseRejected(resp.body)  # autobuy's own typed error
    return resp


session.request_middlewares.register(LoggingMiddleware())  # … or register an instance
# session.request_middlewares.unregister(stable_id_or_instance)  # and remove at runtime

client = Client(tokens=[...], transport=session)  # methods now ride this session
```

`HttpxSession(..., middlewares=[...])` still pre-registers a list at construction. The manager dedups
by class (`stable_id = "<module>.<qualname>"`) — two instances of one middleware class collide, so use
distinct classes for distinct layers (aiogram convention).

**Response validation is its own seam** — `BaseResponseValidator` (`transport/validation.py`) decides
whether a `Response` is an error and which typed one (`validate(response) -> LztError | None`), instead
of a hardcoded status function. The session takes `validator=` (default `StatusResponseValidator`);
chain yours with the default to add body-based checks:

```python
from pylzt.transport.validation import BaseResponseValidator, StatusResponseValidator, ChainResponseValidator


class RejectOnBody(BaseResponseValidator):
  def validate(self, response: Response) -> LztError | None:
    if response.body.get("status") == "error":
      return PurchaseRejected(response.body)  # autobuy's typed error, on a 200
    return None


session = HttpxSession(validator=ChainResponseValidator([StatusResponseValidator(), RejectOnBody()]))
```

The raw `LOLZTEAM` client stays behind the seam (Law 18) — methods, middlewares and validators give
full endpoint access *through* the pool, never around it. `HttpxSession` lazily imports httpx
(`pip install lzt-core[httpx]`); a missing install surfaces as a typed `DependencyMissing`.

## Checklist before you call an extension done

- [ ] You subclassed an ABC and injected it — you did **not** edit a library `.py` (except, at most,
      a one-line `EventType` member or a pollers-seam PR).
- [ ] New store ABC → a `Memory*` impl + a contract test that passes the same suite as the built-ins.
- [ ] Everything typed; `mypy --strict` + `ruff` clean; no raw `dict`/`Any` across a boundary.
- [ ] New error → a typed subclass of the right tree (`LztError` / `EngineError` / `WebError` /
      `DeliveryError`) carrying **args**, chained with `raise … from e`.
- [ ] Money is `Decimal`, datetimes UTC-aware, new routes are GET/POST only.
- [ ] Business code emits events; transports/sinks live behind their ABC — no `httpx.post` in a service.

See also: **`lzt-integration`** (use the library as a consumer), `CONTRIBUTING.md` (the local CI
floor), and each package's `_MODULE.md` / `_MODULE_AUTO.md` (read before touching source).
