<h1 align="center">lzt-eventus</h1>

<p align="right"><b>English</b> · <a href="README.md">Русский</a></p>

<p align="center">
  <strong>Self-hosted event-driven layer over the poll-only lzt.market API — poll, diff, persist, replay.</strong>
</p>

<p align="center">
  <a href="https://github.com/open-lzt/lzt-eventus/blob/main/pyproject.toml"><img src="https://img.shields.io/badge/python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="License"></a>
</p>

**lzt-eventus** is a self-hosted event engine that turns the poll-only
[lzt.market](https://lzt.market) catalog API into domain events with a durable, replayable log —
so any number of subscribers can react (in-process, webhook, SSE/WS, or pull-poll) without
re-polling the marketplace themselves or losing events across a restart.

[Full documentation](docs/README.en.md) · [AI-agent docs](docs/for_ai/index.en.md) ·
[Quickstart](docs/usage/quickstart.en.md) · [Architecture](docs/architecture.en.md) ·
[Extending](docs/extending.en.md) · [Legal / ToS](docs/legal.en.md)

Built on the standalone [`pylzt`](https://github.com/open-lzt/pylzt) SDK:

- **[`pylzt`](https://github.com/open-lzt/pylzt)** — a typed async SDK whose catalog reads flow through a central
  multi-token pool + per-token rate limiter, so a fleet pages the whole catalog
  without ever tripping a `429`. Lives in its own repo; this engine depends on it.
- **`lzt_eventus`** — pollers that diff catalog snapshots into domain events,
  persist them to a durable append-only Postgres log, and a catch-up bus that
  lets any plugin subscribe and resume from its cursor (zero-loss, replayable).
- **[`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk)** — the Python client for
  *consuming* this engine's management API (subscriptions, polling, webhook verification).
  httpx-only, zero coupling to this repo's Postgres/FastAPI stack — install this alone if you're
  writing a webhook receiver or poller, not the whole engine.

> **Legal / ToS.** Read-only catalog + analytics automation only. No brute force,
> no 2FA bypass. See [`docs/legal.md`](docs/legal.en.md).

## Quickstart — clone to running daemon

The engine is a long-lived daemon backed by **self-hosted Postgres 16 + Redis 7**
(both run on the same host via Docker Compose). One script bootstraps it.

**For the lazy — one command, interactive:**

```bash
git clone https://github.com/open-lzt/lzt-eventus lzt-core && cd lzt-core && scripts/quickstart.sh
```

Prompts for your lzt.market token, an optional domain + contact email (for
automatic TLS), generates the admin key itself, then runs the full install —
deps, Postgres + Redis, migrations, the daemon, health gate, TLS if
configured. Ends with a report: health-check URL, admin API key, docs link.

**Manual / scriptable, same result:**

```bash
git clone https://github.com/open-lzt/lzt-eventus lzt-core
cd lzt-core

# 1. Get a lzt.market API token: https://lzt.market/account/api
# 2. Bootstrap (deps check → .env → Postgres+Redis → migrate → start daemon):
scripts/install.sh

# 3. install.sh creates .env from .env.example on first run. Edit it:
#      LZT_TOKENS=["<your-token>"]                 # JSON array, comma-separated for more
#      LZT_ADMIN_API_KEY=<openssl rand -hex 32>    # management-API admin key
#    then re-run (idempotent):
scripts/install.sh
```

Either path brings up the full stack defined in
[`deploy/docker-compose.yml`](deploy/docker-compose.yml) (postgres + redis +
the engine image built from [`deploy/Dockerfile`](deploy/Dockerfile)) and
health-gates the daemon on `/healthz`.

Manage the running stack:

```bash
scripts/status.sh          # engine + Postgres + Redis health, deployed revision
scripts/logs.sh --follow   # stream daemon logs
scripts/update.sh          # rolling update: pull → sync → migrate → restart (health-gated, auto-rollback)
scripts/stop.sh            # graceful SIGTERM
scripts/restart.sh         # stop → start with health gate
```

### Run modes

| Mode | How | Notes |
|---|---|---|
| **Docker Compose** (default) | `scripts/install.sh` | Postgres + Redis + engine all in compose; stores persist to named volumes. |
| **systemd** (bare-metal) | [`deploy/lzt-core.service`](deploy/lzt-core.service) | `ExecStart=uv run python -m lzt_eventus run`, `EnvironmentFile=.env`, graceful SIGTERM, `Restart=on-failure`. Expects Postgres + Redis already on the host. |

### Auto-update (optional)

A config-driven rolling auto-updater polls a git ref and rolls out updates with a
health gate + auto-rollback. Disabled by default — opt in via
[`deploy/autoupdate.yml`](deploy/autoupdate.yml) (`enabled: true`):

```bash
uv run python scripts/autoupdate.py --daemon   # in-process polling loop
# or the systemd timer alternative:
#   deploy/lzt-core-autoupdate.service + .timer  (default: every 5 min)
```

### Domain + automatic TLS (optional)

Set `LZT_DOMAIN` + `LZT_ACME_EMAIL` in `.env` and re-run `scripts/install.sh` —
it fronts the engine with host-level nginx + certbot (`scripts/setup_tls.sh`)
and issues a real Let's Encrypt certificate. Safe on a shared box already
running other sites (adds one vhost, never touches the others). Full guide:
**[docs/deploy.md](docs/deploy.en.md)** (English) /
**[docs/deploy.ru.md](docs/deploy.md)** (Russian).

### Run without Docker

```bash
uv sync --extra engine
uv run python -m lzt_eventus run            # or --dry-run to poll+diff without writing
```

## Operating a running deployment

- **Monitor** — `scripts/status.sh` (engine + Postgres + Redis health, deployed revision),
  `scripts/logs.sh --follow`, or hit `/healthz` / `/readyz` / `/metrics` directly.
- **Update** — `scripts/update.sh` (pull → sync → migrate → restart, health-gated, auto-rollback on failure).
- **Remove** — `docker compose -f deploy/docker-compose.yml down -v` (add `-v` to also drop the
  Postgres/Redis volumes; drop it to keep data for a later re-install).
- **Manage** — the [management API](#management-api-wave-4) (`/subscriptions`, `/events/pending`,
  DLQ inspection) plus `/scalar` for an interactive reference; full script list below.

## Usage

This is a self-hosted system, not a library — the examples below are integration surfaces with a
**running** engine: embed it in your own process, receive its webhooks, or poll its management
API. All I/O is async. Heavier walkthroughs live in the two bundled skills
(`.claude/skills/lzt-integration`, `.claude/skills/lzt-extending`).

`lzt_eventus` polls the catalog through [`pylzt`](https://github.com/open-lzt/pylzt) — the
typed async SDK is a separate dependency with its own README; raw catalog reads
(`client.market.get_lot`, `list_lots`, pagination, DI, error handling) are documented there, not
duplicated here. What belongs in *this* README is what you get from the engine on top of it:
domain events, a durable log, and multi-subscriber delivery.

### Engine — subscribe to events in-process (a `BaseModule` plugin)

Embed the engine in your app and react to domain events. The bus is a **catch-up** dispatcher: each
module has a stable `name` (its cursor key) and resumes exactly where it left off after a restart.

```python
import asyncio
from decimal import Decimal

from lzt_eventus.config import EngineConfig
from lzt_eventus.engine import EventEngine
from lzt_eventus.events.base import DomainEvent, EventType
from lzt_eventus.events.lot import PriceDropped
from lzt_eventus.plugins.module import BaseModule, BaseSubscription


class DealWatcher(BaseModule):
    name = "deal-watcher"  # ← its cursor identity

    def __init__(self) -> None:
        self.subscriptions = [
            BaseSubscription(
                event_types=frozenset({EventType.PRICE_DROPPED}),
                event_cls=PriceDropped,  # narrows the type in handle()
            )
        ]

    async def handle(self, event: DomainEvent) -> None:
        assert isinstance(event, PriceDropped)
        if event.new_price < event.old_price * Decimal("0.85"):
            print("deal:", event.lot.item_id, event.new_price)


async def main() -> None:
    config = EngineConfig()  # reads LZT_* env
    engine, _sessionmaker = EventEngine.build(config, modules=[DealWatcher()])  # Postgres-backed
    await engine.run()  # supervises pollers + bus until stop


asyncio.run(main())
```

For tests / a no-Postgres embed, use in-memory stores (pass your own `Client`):

```python
engine = EventEngine.build_memory(EngineConfig(), client=Client(tokens=["<token>"]),
                                                                    modules=[DealWatcher()])
await engine.drain_once()        # one poll + one pump (deterministic, great for tests)
```

### Engine — register handlers with decorators (`EventRouter`)

Prefer decorators over a `BaseModule` subclass? `EventRouter` *is* a module (one cursor) whose
handlers are bound by `@router.on(...)`. Register it like any other module:

```python
from lzt_eventus.events.base import DomainEvent, EventType
from lzt_eventus.events.lot import PriceDropped
from lzt_eventus.plugins.router import EventRouter


router = EventRouter("price-bot")  # name = its cursor identity


@router.on(EventType.NEW_LOT)
async def on_new_lot(event: DomainEvent) -> None:
    ...


@router.on(EventType.PRICE_DROPPED, event_cls=PriceDropped)
async def on_drop(event: DomainEvent) -> None:
    assert isinstance(event, PriceDropped)  # narrowed by event_cls
    print(event.old_price, "→", event.new_price)


engine.add_module(router)  # or EventEngine.build(config, modules=[router])
```

One handler can cover several types — `@router.on(EventType.NEW_LOT, EventType.LOT_UPDATED)`.

### Engine — add/drop sources and subscribers at runtime

Sources (pollers) and subscribers (modules) can be changed while `run()` is live — no restart:

```python
runner = asyncio.create_task(engine.run())

engine.add_module(DealWatcher())            # picked up on the next bus pump
engine.remove_module("deal-watcher")        # cursor stays committed → safe to re-add later

engine.add_poller(my_source)                # supervisor starts its task immediately
engine.remove_poller("my-source")           # its task stops gracefully
print(engine.poller_names, engine.module_names)
```

### Engine — a custom event source (`BasePoller`)

```python
from lzt_eventus.poller.base import BasePoller


class HeartbeatPoller(BasePoller):
    name = "heartbeat"

    def __init__(self, log, bus) -> None:
        super().__init__(min_cadence=5, max_cadence=60, cadence=10)
        self._log, self._bus = log, bus

    async def poll_once(self) -> int:
        events = await self._scan()  # build + return your DomainEvent instances
        for e in events:
            await self._log.append(e)
        if events:
            self._bus.notify()
        return len(events)


# inject at build time …
engine = EventEngine.build_memory(
    EngineConfig(), client=client, modules=[DealWatcher()],
    extra_pollers=[HeartbeatPoller(log=..., bus=...)]
)
# … or hot-add later: engine.add_poller(HeartbeatPoller(...))
```

### Engine — receive events over a signed webhook (any language)

Use the [`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk) Python client to talk
to the management API — it's the httpx-only counterpart to this repo, no Postgres/FastAPI pulled
in (`pip install lzt-eventus-sdk`). Every example below uses it; raw `curl` works identically
against the same routes for non-Python consumers.

Register a webhook subscription, then verify the HMAC signature on your receiver. Deliveries get
catch-up + retry + DLQ just like in-process modules.

```python
from lzt_eventus_sdk import (
        CategoryScope,
        EventType,
        ManagementClient,
        MarketCategory,
        SubscriptionTransport,
)

async with ManagementClient("http://<engine-host>:27543", api_key=LZT_ADMIN_API_KEY) as mgmt:
        sub = await mgmt.create_subscription(
                transport=SubscriptionTransport.WEBHOOK,
                endpoint="https://you.example/hook",
                event_types=[EventType.NEW_LOT, EventType.PRICE_DROPPED],
                # LZT_CATEGORIES controls which categories the *engine* polls at all — every
                # subscriber shares that pipeline. `scope` narrows what *this* subscriber
                # receives among them; rejected at creation if it can never match `event_types`
                # (e.g. a category scope on `rating_changed`) — see SubscriptionScopeMismatch.
                scope=CategoryScope(category=MarketCategory.STEAM),
        )
        print(sub.secret)  # per-subscription signing secret — one-time, save it now
```

```python
from fastapi import FastAPI, Request, Response
from lzt_eventus.delivery.signing import verify_webhook


app = FastAPI()
SECRET = "<the secret returned above>"


@app.post("/hook")
async def hook(request: Request) -> Response:
    body = await request.body()  # RAW bytes, before parsing
    if not verify_webhook(secret=SECRET, body=body, presented=request.headers.get("X-LZT-Signature")):
        return Response(status_code=401)
    # de-dupe on Idempotency-Key (delivery is at-least-once), then process…
    return Response(status_code=200)  # 2xx acks; non-2xx is retried → DLQ
```

### Engine — poll for pending events instead of subscribing (no webhook/stream needed)

If you'd rather pull than receive a push (no public endpoint to expose, easier to run behind a
firewall/cron), register a `SubscriptionTransport.POLLING` subscription instead of
`WEBHOOK`/`SSE`/`WEBSOCKET`. Each polling subscription tracks its **own** cursor — independent
pollers never race each other, and you can filter by `event_type` per request.

```python
sub = await mgmt.create_subscription(
        transport=SubscriptionTransport.POLLING,
        endpoint="my-cron-poller",
        event_types=[EventType.NEW_LOT, EventType.PRICE_DROPPED],
)
# sub.secret / sub.stream_token are both None — polling is pull-only and already
# gated by the admin key, no push credential to mint.
```

`poll_pending` returns events after the subscription's committed cursor. By default
(`read_all=False`) the cursor is **not** advanced — the same batch replays on retry, so you can
inspect it before committing:

```python
batch = await mgmt.poll_pending(sub.subscription_id, event_type=[EventType.NEW_LOT], limit=100)
for event in batch.items:
        print(event.seq, event.event_type, event.data)
# batch.next_seq / batch.last_read_seq / batch.drained — see lzt-eventus-sdk's PendingBatch
```

Confirm what you've actually processed, either inline (`read_all=True` on `poll_pending` commits
the exact batch scanned) or explicitly against a `seq` boundary — e.g. after only some items in
the batch succeeded downstream:

```python
last_seq = await mgmt.confirm_read(sub.subscription_id, up_to_seq=batch.next_seq)
# idempotent — replaying an older/equal seq is a no-op
```

Every management/polling error is a typed `{"error": "<code>", "detail": {...}, "request_id": "..."}`
envelope (never a bare `HTTPException`). Codes relevant to polling:

| Code | Status | When |
|---|---|---|
| `unknown_event_type` | 400 | `event_type` filter (or `event_types` on create) isn't in the `EventType` catalog. |
| `invalid_limit` | 400 | `limit` isn't a positive integer. |
| `limit_too_large` | 400 | `limit` exceeds `LZT_MAX_QUERY_LIMIT` (default 500). |
| `not_a_polling_subscription` | 400 | `subscription_id` exists but was registered with a push transport. |
| `subscription_not_found` | 404 | `subscription_id` doesn't exist. |

`invalid_limit`/`limit_too_large` are enforced by `LimitValidationMiddleware`
([`web/middlewares/limits.py`](src/lzt_eventus/web/middlewares/limits.py)) — it reads `?limit=`
straight off the query string **before** any route runs, so every current and future
`limit`-accepting endpoint gets the same bound and the same error shape for free.

### Engine — one-call local devkit (scripts, examples, quick experiments)

`local_eventus` is the progressive-disclosure quickstart for the web/subscription side: one
`async with` gets a real, live-polling engine **and** its management API on an ephemeral port —
no Postgres/Redis, no manual `EngineHandle` wiring. Everything it wires (`client`, `config`,
`consumers`, `extra_sources`, dedup, stores) is still the same overridable seam `build_memory`
already exposes — this just supplies working defaults for the rest. See
[`examples/autobuy`](examples/autobuy) for a full ~10-line consumer built on it.

```python
from lzt_eventus.devkit import local_eventus
from pylzt.types import Category
from lzt_eventus_sdk import CategoryScope, EventType, ManagementClient, SubscriptionTransport


async with local_eventus(tokens=["<token>"]) as server:
    async with ManagementClient(server.base_url, api_key=server.api_key) as mgmt:
        sub = await mgmt.create_subscription(
            transport=SubscriptionTransport.POLLING, endpoint="quickstart",
            event_types=[EventType.NEW_LOT], scope=CategoryScope(category=Category.TELEGRAM),
        )
        batch = await mgmt.poll_pending(sub.subscription_id, limit=100)
        for event in batch.items:
            print(event.data["lot"]["item_id"], event.data["lot"]["price"])
```

### Engine — swap a store backend (subclass + inject)

Every store is an ABC with a `Memory*` default and a `Postgres*` impl — subclass for a new backend
and pass a `Stores` bundle straight to the constructor:

```python
from lzt_eventus.engine import EventEngine, Stores
from lzt_eventus.cursor.memory import MemoryCursorStore
from lzt_eventus.bus.dlq import MemoryDeadLetterStore
from lzt_eventus.baseline.store import MemoryLastSeenStore


last_seen = MemoryLastSeenStore()
stores = Stores(
    log=MyRedisEventLog(...), last_seen=last_seen,
    cursor=MemoryCursorStore(), dlq=MemoryDeadLetterStore()
)
engine = EventEngine(client=client, stores=stores, config=EngineConfig(), modules=[DealWatcher()])
```

## Operations scripts

All under [`scripts/`](scripts/) — `set -euo pipefail`, colour output, `--help`, idempotent.

| Script | Purpose |
|---|---|
| `quickstart.sh` | Interactive one-command bootstrap: prompt → `.env` → `install.sh` → report. |
| `install.sh` | One-shot bootstrap: clean host → running daemon. |
| `setup_tls.sh` | Host nginx + certbot vhost/cert for `LZT_DOMAIN` (called by `install.sh`). |
| `update.sh` | Rolling update with health gate + auto-rollback. |
| `rollback.sh` | Revert the last update (code + one migration step + restart). |
| `migrate.sh` | `alembic upgrade head` (idempotent). |
| `seed.sh` | Load recorded catalog pages offline (`--file`) for dev/CI. |
| `replay.sh` | `--consumer X --from-seq N` — rewind a cursor to backfill. |
| `redrive.sh` | `--consumer X` — re-inject dead-lettered events after a fix. |
| `prune.sh` | Retention: delete event-log rows below the consumer watermark. |
| `backup.sh` / `restore.sh` | pg_dump / pg_restore the event log (round-trippable). |
| `stop.sh` / `restart.sh` / `status.sh` / `logs.sh` | Lifecycle + observability. |
| `autoupdate.py` | Config-driven rolling auto-updater (typed, unit-tested). |
| `health.py` | Standalone `/healthz` + `/readyz` probe (used by the update gate). |

## Configuration

Every variable is read by `EngineConfig` with the `LZT_` prefix
([`src/lzt_eventus/config.py`](src/lzt_eventus/config.py)). Full annotated list
in [`.env.example`](.env.example) — copy it to `.env`.

Required vars marked `*`; everything else has a working default.

| Var | Default | Meaning |
|---|---|---|
| `LZT_TOKENS` `*` | `[]` | lzt.market token(s), JSON array. [Get one](https://lzt.market/account/api). |
| `LZT_ADMIN_API_KEY` `*` | — | Management-API key. `openssl rand -hex 32`. |
| `LZT_DATABASE_URL` | `postgresql://…` | Postgres DSN. |
| `LZT_REDIS_URL` | `redis://localhost:6379/0` | Redis URL. |
| `LZT_CATEGORIES` | `["steam"]` | Categories to poll, JSON array. |
| `LZT_MIN/MAX/DEFAULT_CADENCE` | `6` / `120` / `30` | Poll cadence bounds, seconds. |
| `LZT_PER_PAGE` | `50` | Catalog page size. |
| `LZT_DISAPPEAR_POLLS` | `3` | Missing polls before sold. |
| `LZT_CONFIRM_BUDGET_FRACTION` / `_BATCH_SIZE` | `0.25` / `50` | Confirmation rate budget + batch. |
| `LZT_SEEN_TTL_SECONDS` | `86400` | Dedup window for seen lots. |
| `LZT_BATCH_SIZE` / `LZT_BATCH_LINGER` | `50` / `0.05` | Ingest batching. |
| `LZT_MAX_HANDLE_ATTEMPTS` | `5` | Deliveries before DLQ. |
| `LZT_RETENTION_MONTHS` | `3` | Event-log retention. |
| `LZT_MAX_SINK_LAG` | `100000` | Max consumer lag before alarm. |
| `LZT_WARN_WINDOW_HOURS` | `24` | Analytics warning window. |
| `LZT_DEAL_THRESHOLD` | `0.85` | `price < ai_price * threshold`. |
| `LZT_HEALTH_HOST` / `_PORT` | `0.0.0.0` / `27543` | HTTP server (`/healthz`, `/metrics`). |
| `LZT_POSTGRES_PORT` / `LZT_REDIS_PORT` | `27542` / `27541` | Compose host ports (loopback). |
| `LZT_ADVISORY_LOCK_KEY` / `LZT_RUN_ID` | `1819571811` / `engine` | Single-writer election + run id. |
| `LZT_MAX_QUERY_LIMIT` | `500` | Max `?limit=` on any endpoint. |
| `LZT_WEB_DOCS_ENABLED` | `true` | Serve `/docs` + `/scalar`. |

Non-standard health port on purpose — see [deploy guide](docs/deploy.en.md).

## Management API (wave 4)

An admin-key-guarded HTTP API exposes subscription management (register/list
consumers, inspect cursors and DLQ) plus `/events/pending` + `/events/read_events`
for pull-based polling (see [above](#engine--poll-for-pending-events-instead-of-subscribing-no-webhookstream-needed)).
Authenticate with the `LZT_ADMIN_API_KEY`
you set in `.env`. **By design the API is POST/GET only** — no PUT/PATCH/DELETE
(CI enforces this over `src/lzt_eventus/web`). See [`ROADMAP.md`](ROADMAP.md).

See [`AGENTS.md`](AGENTS.md) for the wire-contract-sync rule that applies to any
separate-repo consumer of this API (e.g. [`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk)).

**Docs.** The engine hosts its own interactive reference — no external doc site,
no scalar.com account:

- `http://<engine-host>:27543/scalar` — [Scalar](https://github.com/scalar/scalar) reference
  (reads `/openapi.json`; every route, DTO and error code above, browsable/testable).
- `http://<engine-host>:27543/docs` — Swagger UI (FastAPI's built-in alternative).

Both are gated by `LZT_WEB_DOCS_ENABLED` (default `true`) — set it `false` to serve neither
on a production deployment you don't want to expose a doc UI on.

## Branch protection

`main` is protected: every PR must pass CI
([`.github/workflows/ci.yml`](.github/workflows/ci.yml) — ruff + ruff format +
`mypy --strict` + `pytest --cov-fail-under=80` + gitleaks + pip-audit) and carry a
CODEOWNERS review before merge. Configure under **Settings → Branches → Branch
protection rules**: require status checks, require a Code Owner review, no direct
pushes to `main`.

## For AI agents building on this repo

Two Claude Code skills ship in [`.claude/skills/`](.claude/skills/) so an agent can pick up the
project's surface without reverse-engineering it:

- [`lzt-integration`](.claude/skills/lzt-integration/SKILL.md) — **use** the library: read the
  catalog with `Client`, subscribe in-process via a `BaseModule` plugin, receive signed webhooks, or
  poll `/events/pending` for a pull-based alternative.
- [`lzt-extending`](.claude/skills/lzt-extending/SKILL.md) — **extend the core** by subclass + inject
  (new event type, route, source, store/transport backend) without editing library source.

## Status & contributing

See [`docs/architecture.md`](docs/architecture.en.md) for the current architecture and
[`ROADMAP.md`](ROADMAP.md) for scope and non-goals. Contribution setup, the local
CI floor, and conventions are in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Community

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines and how to submit PRs. Use
[issues](https://github.com/open-lzt/lzt-eventus/issues/new/choose) for bugs and feature requests.

<a href="https://github.com/zlexdev"><img src="https://github.com/zlexdev.png" width="48" height="48" style="border-radius:50%" alt="zlexdev"></a>

## License & legal

[MIT](LICENSE). Read the [legal / ToS disclaimer](docs/legal.en.md) before use —
read-only catalog + analytics automation only; you are responsible for compliance
with the lzt.market Terms of Service.
