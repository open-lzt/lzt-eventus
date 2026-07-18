# Roadmap

lzt-core turns the poll-only lzt.market API into a durable, replayable event
stream. Delivery is staged across four waves.

## Wave 1 — SDK foundation ✅

- Typed async SDK (`lzt_core`) over the lzt.market catalog.
- Central multi-token pool + per-token rate limiter (fleet pages the whole
  catalog without tripping `429`).
- Demo CLI (`python -m lzt_core demo-list`).

## Wave 2 — Event engine (core release) 🚧

- Pollers diff catalog snapshots into domain events (new / price-change /
  disappeared-sold / deal-detected).
- Durable append-only Postgres event log (`UNIQUE(event_id)` dedup, watermark-gated retention).
- Catch-up bus: cursor-based, resumable, zero-loss; DLQ for poison events.
- Daemon (`python -m event_engine run`) with `/healthz`, `/readyz`, `/metrics`.
- Ops + governance floor (this milestone): install/update/rollback/backup,
  CI gates, docs, license.

## Wave 3 — Delivery layer

- Sinks (webhook / queue) with outbox + retry + DLQ.
- Proxy pool for outbound delivery.
- Load tests (Locust): documented req/s, p95, zero-429 under sustained paging.

## Wave 4 — Management API

- Admin-key-guarded HTTP API (`LZT_ADMIN_API_KEY`).
- Subscriptions endpoints (register/list consumers, inspect cursors/DLQ).
- **POST/GET only** by design (no PUT/PATCH/DELETE — enforced in CI).

---

## NON-GOALS (explicitly out of scope, permanently)

- **No brute force, no 2FA/security bypass, no account takeover.** This is not an
  attack tool. See [`docs/legal.md`](docs/legal.md).
- **Market-only / lolz-only.** Scope is the lzt.market / lolz.team catalog. No
  other marketplaces, no general-purpose scraping framework.
- **Read + analytics only.** Catalog reads, event derivation, and outbound
  delivery of events you're entitled to — never writes/mutations to the platform.
- **Web + API surface only.** A daemon plus an HTTP management API. No desktop GUI,
  no mobile app, no browser extension.
