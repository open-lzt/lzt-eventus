# Architecture

<p align="right"><b>English</b> · <a href="architecture.md">Русский</a></p>

Current-state reference, grounded in the actual package layout (`src/lzt_eventus/`) —
not a historical plan. For per-package detail, each package has its own `_MODULE.md`.

## The shape

```
pylzt (separate repo, git dep)          lzt-eventus (this repo)                     downstream (separate repos)
┌──────────────────────────┐   poll   ┌──────────────────────────────────┐   push   ┌────────────────────┐
│ typed async SDK           │ ───────▶ │ sources/ → diff/ → events/       │ ───────▶ │ webhook receiver     │
│ token pool + rate limit   │          │ → log/ (durable) → bus/ (cursor) │          │ (any language)       │
│ client.market/.forum/...  │          │ → delivery/ (sinks) / web/ (API) │   pull   ├────────────────────┤
└──────────────────────────┘          └──────────────────────────────────┘ ───────▶ │ lzt-eventus-sdk     │
                                                                                       │ (Python SDK client)  │
                                                                                       └────────────────────┘
```

## Data path (poll → durable event → delivery)

1. **`sources/`** — one poller per concern (`category`, `confirm`, `conversations`,
   `guarantee`, `notifications`, `payments`, `rating`). Each calls `pylzt`'s
   `Client.market.*`/`.forum.*` and hands the response to a differ or a
   notification/payment-operation parser.
2. **`diff/`** — `SnapshotDiffer` (pure, sans-I/O) compares the current poll against
   the durable baseline and emits `NewLotAppeared` / `PriceDropped` / `LotUpdated`;
   `LotDisappeared` needs the miss-counter + confirm-poll, so the source owns that
   decision, not the differ.
3. **`events/`** — the `DomainEvent` taxonomy. `EventType` is the full catalog (one
   `StrEnum`); concrete event classes are grouped by family (`lot.py`, `payment.py`,
   `notification.py`, `message.py`, `reputation.py`, `account.py`, `marker.py`).
   Extending it is registry-free for lot/lifecycle events (subclass + `EVENT_TYPE`)
   and dict-driven for notification/payment sub-events (`_CONTENT_TYPE_EVENTS` /
   `_BY_OPERATION_TYPE` in `events/notification.py` / `events/payment.py`) — a new
   sub-event is a dict entry, never a branch.
4. **`log/`** — `BaseEventLog` (Memory + Postgres), append-only, `UNIQUE(event_id)`
   dedup, gapless committed `seq`.
5. **`bus/`** — `CatchUpBus`: one supervised worker per consumer, pulls
   `log.read_after(cursor)`, replays in seq order. Sequential *within* a consumer
   (ordering/cursor invariant), concurrent *across* consumers. Poison events park in
   the DLQ (`bus/dlq.py`) after `max_handle_attempts`; the cursor still advances.
6. **`consumers/`** — the plugin contract (`BaseConsumer` + `BaseSubscription`).
   `LoggingConsumer` is the open-closed proof: a real subscriber, zero engine edits.
7. **`delivery/` + `web/`** — subscriptions turn into cursor-bearing bus consumers.
   `Subscription` (delivery/subscription.py) carries a typed `scope`
   (`NoScope`/`CategoryScope`/`AccountScope` — what it receives) and `ctx`
   (`WebhookCtx`/`WebSocketCtx`/`SseCtx`/`PollingCtx` — per-transport delivery knobs,
   e.g. `PollingCtx.poll_delay_seconds`). Four transports: **webhook** (push, HMAC-signed,
   retry+DLQ via the extracted `libs/webhook_engine`), **polling** (pull,
   `GET /events/pending` + explicit `read_events` confirm, own cursor per subscriber),
   **SSE**/**WebSocket** (pull-stream). `web/` is the FastAPI management API
   (routes → services → repos → orm) — admin-key gated, POST/GET only by design.

## Supporting packages

- **`cursor/`** — `BaseCursorStore`: one resumable position per consumer
  (`sink:<subscription_id>` for delivery sinks).
- **`dedup/`** — `BaseSeenCache`: pre-filter before the durable log append.
- **`baseline/`** — `BaseLastSeenStore`: the durable snapshot `SnapshotDiffer` diffs
  against.
- **`account/`** — token-account reconciliation for per-account sources (rating
  today; payments/notifications/conversations/guarantee once wired the same way).
- **`orm/`** — SQLAlchemy declarative models for every durable store; migrations in
  `alembic/versions/`.
- **`daemon/`** — advisory lease (single-owner) + observability wiring
  (`/healthz`/`/readyz`/`/metrics`).
- **`engine.py`** — `EventEngine`: assembles the whole graph. `build()` (real
  Postgres/webhook daemon) vs `build_memory()` (embedded, zero-infra — tests, or a
  standalone script that wants engine-grade delivery without running the daemon).
  `drain_once()` is one poll-all-categories + one bus-pump cycle, used by tests and
  `--dry-run`.

## Cross-repo boundary

This repo owns the wire contract (`web/schemas/dtos.py`, `web/base/error_codes.py`).
Consumer repos mirror it and must ship together with any contract change (see
`AGENTS.md`):

- **[`lzt-eventus-sdk`](https://github.com/open-lzt/lzt-eventus-sdk)** — the Python
  client. httpx-only, zero coupling to this repo's Postgres/FastAPI stack.

A single-process script that wants live events but not durable/crash-recoverable
delivery to *multiple* consumers doesn't need any of `delivery/`/`web/` — see
`examples/autobuy/_MODULE.md` for the lighter pattern (`EventEngine.build_memory()` +
`BaseConsumer`, or bypass the engine entirely for a one-off).

## See also

`ROADMAP.md` — scope and non-goals (predates several now-shipped milestones; treat
wave markers there as historical, not current status). Per-package `_MODULE.md` files
— control-flow detail this doc deliberately omits. `docs/extending.md` — the seam map
for adding a new event type / source / store backend / transport.
