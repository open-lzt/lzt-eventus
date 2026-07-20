<h1 align="center">lzt-eventus</h1>

<p align="right"><b>English</b> · <a href="README.md">Русский</a></p>

<p align="center">
  <strong>A self-hosted event layer over the poll-only lzt.market API.</strong>
</p>

<p align="center">
  <a href="https://github.com/open-lzt/lzt-eventus/blob/main/pyproject.toml"><img src="https://img.shields.io/badge/python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="License"></a>
</p>

lzt.market has no webhooks. It has a catalog you can poll.

**Poll-only** means: to learn that a lot appeared, you poll the listing yourself, in a loop, and spot the change yourself.

This engine does that polling once — for everyone.

It polls the catalog, diffs the snapshots, turns the difference into **events**, and appends them to a durable log on Postgres.

**Durable log** — an append-only table: the event is written for good, it carries a gapless `seq`, and you can re-read it a month later.

Anything can then subscribe to that log: your own process, a webhook on another host, an SSE/WS stream, a cron poller.

The trap it closes: **a subscriber does not lose events across a restart**. Each one owns a cursor in the log — it dies, comes back, and resumes exactly where it stopped.

[Full documentation](docs/README.en.md) · [Quickstart](docs/usage/quickstart.en.md) · [Architecture](docs/architecture.en.md) · [Extending](docs/extending.en.md) · [AI-agent docs](docs/for_ai/index.en.md) · [Legal / ToS](docs/legal.en.md)

> **ToS.** Catalog reads and analytics automation only. No brute force, no 2FA bypass. See [`docs/legal.en.md`](docs/legal.en.md).

---

## Neighbouring projects

The engine never talks to the market itself — an SDK does that for it.

- **[`pylzt`](https://github.com/open-lzt/pylzt)** — a typed async SDK over the market API. Token pool plus a per-token rate limiter, so polling the whole catalog never trips `429`. Its own repo; a dependency of this one.
- **[`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk)** — a client for *this* engine's management API: subscriptions, polling, webhook verification. httpx-only, no Postgres and no FastAPI. Install it when you're writing an event receiver, not running the engine.

---

## Event catalog

`EventType` holds **42** names. **31** are emitted today; the other 11 are reserved names with no implementation, listed at the end.

A subscription filters on these strings: `event_types=[EventType.NEW_LOT, ...]`.

### Catalog and lots

Sourced from diffing listing snapshots.

| Event | When |
|---|---|
| `new_lot` | A lot appeared that wasn't in the previous snapshot |
| `price_dropped` | The lot got cheaper; the event carries both the old and the new price |
| `lot_updated` | Something other than the price changed |
| `lot_disappeared` | The lot left the listing — sold or pulled; the guess lands in `reason` |
| `snapshot_initialized` | Cold-start marker: one event instead of a flood of `new_lot` on the very first poll |

### A lot in a deal

Sourced from market notifications, not from the diff.

| Event | When |
|---|---|
| `lot_reserved` | A buyer put the lot on hold |
| `purchase_confirmed` | The seller confirmed a purchase. This is **not** `item_sold` — that one is about money, this one about the deal |

### Money

Balance operations on the account.

| Event | When |
|---|---|
| `income_received` | Money came in |
| `expense_recorded` | Money went out |
| `balance_refilled` | Balance topped up |
| `balance_withdrawn` | Balance withdrawn |
| `item_purchased` | An item was bought |
| `item_sold` | An item was sold — the money side of the deal |
| `money_transferred` | Transfer sent |
| `money_received` | Transfer received |
| `internal_purchase` | An on-platform internal purchase |
| `hold_claimed` | Funds claimed out of hold |
| `auto_payment_triggered` | An auto-payment fired |
| `balance_exchanged` | Balance currency exchanged |

### Account and guarantee

| Event | When |
|---|---|
| `guarantee_expiring` | The guarantee on a bought account is about to run out |
| `account_invalid` | The account stopped being valid |
| `dispute_opened` | A dispute was opened |
| `claim_filed` | A claim was filed |

### Conversations, reputation, notifications

| Event | When |
|---|---|
| `new_conversation` | A new conversation started |
| `new_message` | A new message in a conversation |
| `rating_changed` | The rating changed |
| `market_notification_received` | A market notification |
| `forum_notification_received` | A forum notification |

### Invoices

The one group that does **not** arrive by polling — it comes in on `POST /inbound` as an HMAC-verified inbound webhook.

| Event | When |
|---|---|
| `invoice_created` | Invoice created |
| `invoice_paid` | Invoice paid |
| `invoice_expired` | Invoice expired |

> **Unverified against a real webhook.** The body format and the signature scheme are implemented defensively, on assumption. Reconcile them against a genuine captured webhook before relying on this in production — see [`web/routes/inbound.py`](src/lzt_eventus/web/routes/inbound.py).

### Reserved names

Present in `EventType`, not emitted yet: `payout_requested`, `transfer_held`, `transfer_cancelled`, `reserve_expired`, `purchase_cancelled`, `deal_detected`, `price_vs_ai_changed`, `inventory_revalued`, `discount_requested`, `discount_approved`, `discount_declined`.

You can subscribe to them — nothing will arrive.

To add your own event: [`docs/extending.en.md`](docs/extending.en.md). The codec is registry-free, so a `DomainEvent` subclass plus an `EventType` member is the whole job.

**Remember this one:** `event_id` is deterministic — uuid5 of `(aggregate_id, event_type, content_hash, poll_epoch)`. The same logical fact always hashes to the same id, so a re-poll after a crash doesn't double-emit; it collides on the log's UNIQUE constraint instead.

---

## Quickstart

The engine is a long-lived daemon on **Postgres 16 + Redis 7**, both brought up on the same host via Docker Compose.

You need an lzt.market token: https://lzt.market/account/api

**One command, with prompts:**

```bash
git clone https://github.com/open-lzt/lzt-eventus lzt-core && cd lzt-core && scripts/quickstart.sh
```

It asks for the token, optionally a domain and email for TLS, generates the admin key, installs everything, and gates on `/healthz`. It prints the key and the links at the end.

**Same thing, scriptable:**

```bash
git clone https://github.com/open-lzt/lzt-eventus lzt-core
cd lzt-core

scripts/install.sh          # creates .env from .env.example and stops

# fill in .env:
#   LZT_TOKENS=["<token>"]                    # JSON array
#   LZT_ADMIN_API_KEY=<openssl rand -hex 32>  # management API key

scripts/install.sh          # run again — idempotent
```

Either path brings up the stack in [`deploy/docker-compose.yml`](deploy/docker-compose.yml).

**Without Docker:**

```bash
uv sync --extra engine
uv run python -m lzt_eventus run     # --dry-run: polls and diffs, writes nothing
```

**Bare-metal under systemd:** [`deploy/lzt-core.service`](deploy/lzt-core.service). Expects Postgres and Redis to already be on the host.

**Domain and TLS:** set `LZT_DOMAIN` + `LZT_ACME_EMAIL` in `.env` and re-run `install.sh` — it brings up nginx + certbot and issues a Let's Encrypt certificate. It adds one vhost and leaves other sites on the host alone. Guide: [`docs/deploy.en.md`](docs/deploy.en.md).

**Auto-update:** off. Enable it in [`deploy/autoupdate.yml`](deploy/autoupdate.yml) — it polls a git ref and rolls updates out with a health gate and automatic rollback.

---

## How to subscribe

Four transports, one semantics: own cursor, catch-up after downtime, retries, DLQ.

**DLQ** — dead-letter queue: an event that fails to deliver within `LZT_MAX_HANDLE_ATTEMPTS` attempts is parked here instead of being lost or blocking the queue.

### 1. In-process — the engine inside your application

```python
import asyncio
from decimal import Decimal

from lzt_eventus.config import EngineConfig
from lzt_eventus.engine import EventEngine
from lzt_eventus.events.base import DomainEvent, EventType
from lzt_eventus.events.lot import PriceDropped
from lzt_eventus.plugins.module import BaseModule, BaseSubscription


class DealWatcher(BaseModule):
    name = "deal-watcher"  # ← this IS its cursor

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
    engine, _ = EventEngine.build(EngineConfig(), modules=[DealWatcher()])
    await engine.run()


asyncio.run(main())
```

`name` is the cursor identity. Change the string and the module starts reading the log from the beginning.

Same module, decorators instead of a subclass — `EventRouter("price-bot")` with `@router.on(EventType.NEW_LOT)`. One router = one cursor.

Modules and pollers can be added and removed while `run()` is live: `engine.add_module(...)`, `engine.remove_poller(...)`. A removed module's cursor stays committed, so bringing it back later is safe.

For tests — no Postgres:

```python
engine = EventEngine.build_memory(EngineConfig(), client=client, modules=[DealWatcher()])
await engine.drain_once()   # one poll plus one bus pump, deterministic
```

### 2. Webhook — a receiver on another host, any language

```python
from lzt_eventus_sdk import CategoryScope, EventType, ManagementClient, MarketCategory, SubscriptionTransport

async with ManagementClient("http://<host>:27543", api_key=LZT_ADMIN_API_KEY) as mgmt:
    sub = await mgmt.create_subscription(
        transport=SubscriptionTransport.WEBHOOK,
        endpoint="https://you.example/hook",
        event_types=[EventType.NEW_LOT, EventType.PRICE_DROPPED],
        scope=CategoryScope(category=MarketCategory.STEAM),
    )
    print(sub.secret)  # signing secret — returned ONCE, save it now
```

`scope` narrows what **this** subscriber receives. What the engine polls at all is `LZT_CATEGORIES`, and that pipeline is shared by everyone. A scope that could never match its `event_types` (a category scope on `rating_changed`, say) is rejected at creation.

Verify the signature on your side:

```python
from fastapi import FastAPI, Request, Response
from lzt_eventus.delivery.signing import verify_webhook

app = FastAPI()


@app.post("/hook")
async def hook(request: Request) -> Response:
    body = await request.body()  # RAW bytes, before parsing
    if not verify_webhook(secret=SECRET, body=body, presented=request.headers.get("X-LZT-Signature")):
        return Response(status_code=401)
    # delivery is at-least-once → dedupe on Idempotency-Key, then handle
    return Response(status_code=200)  # 2xx acks; non-2xx retries → DLQ
```

### 3. SSE / WebSocket — streaming

`SubscriptionTransport.SSE` or `.WEBSOCKET` at creation. Everything else behaves like the webhook.

### 4. Polling — pull instead of push

For when you have nothing to expose: behind a firewall, from cron.

```python
sub = await mgmt.create_subscription(
    transport=SubscriptionTransport.POLLING,
    endpoint="my-cron-poller",
    event_types=[EventType.NEW_LOT],
)
# sub.secret and sub.stream_token are both None — polling is already gated by the
# admin key, so there's no push credential to mint

batch = await mgmt.poll_pending(sub.subscription_id, limit=100)
for event in batch.items:
    print(event.seq, event.event_type, event.data)

await mgmt.confirm_read(sub.subscription_id, up_to_seq=batch.next_seq)
```

The trap: by default (`read_all=False`) `poll_pending` does **not** advance the cursor. The same batch comes back on retry — deliberately, so you can inspect it before committing.

Acknowledge either inline (`read_all=True` commits exactly what it scanned) or explicitly via `confirm_read` on a `seq` boundary, if only part of the batch succeeded. `confirm_read` is idempotent: re-sending an older or equal `seq` is a no-op.

Management API errors are always a typed envelope, `{"error": "<code>", "detail": {...}, "request_id": "..."}` — never a bare `HTTPException`:

| Code | Status | When |
|---|---|---|
| `unknown_event_type` | 400 | The filter isn't in the `EventType` catalog |
| `invalid_limit` | 400 | `limit` isn't a positive integer |
| `limit_too_large` | 400 | `limit` exceeds `LZT_MAX_QUERY_LIMIT` (default 500) |
| `not_a_polling_subscription` | 400 | The subscription exists but was registered with a push transport |
| `subscription_not_found` | 404 | No such subscription |

The `limit` bound is enforced by `LimitValidationMiddleware`, which reads `?limit=` off the query string before any route runs — so every current and future endpoint gets the same ceiling for free.

### Devkit — the engine and its API in one `async with`

For scripts and experiments: a live, actually-polling engine plus its management API on an ephemeral port, with no Postgres and no Redis.

```python
from lzt_eventus.devkit import local_eventus

async with local_eventus(tokens=["<token>"]) as server:
    async with ManagementClient(server.base_url, api_key=server.api_key) as mgmt:
        ...
```

A full ~10-line consumer built on it — [`examples/autobuy`](examples/autobuy).

---

## Management API

An HTTP API behind the admin key (`LZT_ADMIN_API_KEY`): subscriptions, cursors, DLQ inspection, plus `/events/pending` and `/events/read_events` for pull-based polling.

**POST and GET only** — no PUT/PATCH/DELETE, enforced in CI.

The engine hosts its own reference; no external doc site is involved:

- `http://<host>:27543/scalar` — [Scalar](https://github.com/scalar/scalar); every route and DTO is browsable and testable
- `http://<host>:27543/docs` — Swagger UI

Both are gated by `LZT_WEB_DOCS_ENABLED=false`.

The wire-contract sync rule with [`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk) lives in [`AGENTS.md`](AGENTS.md).

---

## Configuration

Everything is read by `EngineConfig` under the `LZT_` prefix. The annotated full list is [`.env.example`](.env.example).

Required ones are marked `*`; everything else has a working default.

| Variable | Default | Meaning |
|---|---|---|
| `LZT_TOKENS` `*` | `[]` | lzt.market token(s), JSON array |
| `LZT_ADMIN_API_KEY` `*` | — | Management API key |
| `LZT_DATABASE_URL` | `postgresql://…` | Postgres DSN |
| `LZT_REDIS_URL` | `redis://localhost:6379/0` | Redis URL |
| `LZT_CATEGORIES` | `["steam"]` | Which categories to poll |
| `LZT_MIN/MAX/DEFAULT_CADENCE` | `6` / `120` / `30` | Poll cadence bounds, seconds |
| `LZT_PER_PAGE` | `50` | Catalog page size |
| `LZT_DISAPPEAR_POLLS` | `3` | Polls a lot must be missing before it counts as gone |
| `LZT_CONFIRM_BUDGET_FRACTION` / `_BATCH_SIZE` | `0.25` / `50` | Confirmation budget and batch |
| `LZT_SEEN_TTL_SECONDS` | `86400` | Dedup window for seen lots |
| `LZT_BATCH_SIZE` / `LZT_BATCH_LINGER` | `50` / `0.05` | Ingest batching |
| `LZT_MAX_HANDLE_ATTEMPTS` | `5` | Delivery attempts before DLQ |
| `LZT_RETENTION_MONTHS` | `3` | Event-log retention |
| `LZT_MAX_SINK_LAG` | `100000` | Consumer lag before it alarms |
| `LZT_WARN_WINDOW_HOURS` | `24` | Analytics warning window |
| `LZT_DEAL_THRESHOLD` | `0.85` | `price < ai_price * threshold` |
| `LZT_HEALTH_HOST` / `_PORT` | `0.0.0.0` / `27543` | HTTP server (`/healthz`, `/metrics`) |
| `LZT_POSTGRES_PORT` / `LZT_REDIS_PORT` | `27542` / `27541` | Compose host ports, on loopback |
| `LZT_ADVISORY_LOCK_KEY` / `LZT_RUN_ID` | `1819571811` / `engine` | Single-writer election, run id |
| `LZT_MAX_QUERY_LIMIT` | `500` | `?limit=` ceiling on every endpoint |
| `LZT_WEB_DOCS_ENABLED` | `true` | Serve `/docs` and `/scalar` |

The port is non-standard on purpose — see the [deploy guide](docs/deploy.en.md).

---

## Scripts

All under [`scripts/`](scripts/): `set -euo pipefail`, `--help`, idempotent.

| Script | Purpose |
|---|---|
| `quickstart.sh` | Interactive bootstrap: prompts → `.env` → `install.sh` → report |
| `install.sh` | Clean host → running daemon, in one pass |
| `setup_tls.sh` | nginx + certbot for `LZT_DOMAIN`, called from `install.sh` |
| `update.sh` | Rolling update with health gate and automatic rollback |
| `rollback.sh` | Revert the last update: code, one migration step, restart |
| `migrate.sh` | `alembic upgrade head` |
| `seed.sh` | Load recorded catalog pages offline, for dev/CI |
| `replay.sh` | `--consumer X --from-seq N` — rewind a cursor to backfill |
| `redrive.sh` | `--consumer X` — re-inject dead-lettered events after a fix |
| `prune.sh` | Retention: drop event-log rows below the consumer watermark |
| `backup.sh` / `restore.sh` | pg_dump / pg_restore of the event log |
| `status.sh` / `logs.sh` / `stop.sh` / `restart.sh` | Lifecycle and observability |
| `autoupdate.py` | Config-driven rolling auto-updater |
| `health.py` | Standalone `/healthz` + `/readyz` probe; the update gate uses it |

Tear it all down: `docker compose -f deploy/docker-compose.yml down -v`. Drop the `-v` and the Postgres/Redis data survives a reinstall.

---

## For AI agents

Two Claude Code skills live in [`.claude/skills/`](.claude/skills/) so an agent doesn't have to reverse-engineer the project:

- [`lzt-integration`](.claude/skills/lzt-integration/SKILL.md) — using it: reading the catalog, subscribing in-process, receiving webhooks, polling.
- [`lzt-extending`](.claude/skills/lzt-extending/SKILL.md) — extending the core by subclassing and injection: a new event type, route, source, store or transport backend, without editing library source.

---

## Contributing

`main` is protected: a PR must pass CI ([ruff, ruff format, `mypy --strict`, `pytest --cov-fail-under=80`, gitleaks, pip-audit](.github/workflows/ci.yml)) and a CODEOWNERS review.

Conventions and the local CI floor — [`CONTRIBUTING.md`](CONTRIBUTING.md). Scope and non-goals — [`ROADMAP.md`](ROADMAP.md). Bugs and feature requests — [issues](https://github.com/open-lzt/lzt-eventus/issues/new/choose).

<a href="https://github.com/zlexdev"><img src="https://github.com/zlexdev.png" width="48" height="48" style="border-radius:50%" alt="zlexdev"></a>

## License

[MIT](LICENSE). Read the [legal / ToS disclaimer](docs/legal.en.md) before use — catalog reads and analytics automation only; staying within lzt.market's terms is on you.
