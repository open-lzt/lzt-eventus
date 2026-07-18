# lzt_eventus.delivery — webhook-push delivery

Turns webhook subscriptions into **cursor-bearing consumers of the durable log**, so
every matching event is HMAC-signed and POSTed to the subscriber's endpoint with
at-least-once, resume-after-restart semantics. SSE/WS are *pull*; this is the *push* leg.

## Public surface
- `WebhookDelivery` — the facade the engine wires (`EventEngine.build` / `build_memory`).
  Owns a dedicated `CatchUpBus` (`sink:<id>` cursor namespace, `max_handle_attempts=1`)
  fed by the dispatcher. `pump_once()` / `run(stop)` / `notify()`.
- `BaseWebhookTransport` — I/O seam (Law 10). `HttpxWebhookTransport` (lazy httpx,
  Law 23; never leaks the httpx type, Law 18) and `RecordingWebhookTransport` (the
  in-memory test double, Law 11).
- `verify_webhook(secret, body, header)` / `sign_webhook` / `signature_header` — symmetric
  HMAC-SHA256 so a receiver verifies with the same primitive the engine signs with.
- `WebhookDeliveryError` / `WebhookTransportError` — typed, carrying args.

## How it works
1. `WebhookDispatcher.consumers()` is the bus's `consumer_provider`: each pump it lists the
   **active webhook** subscriptions and maps each to a cached `WebhookSink`, rebuilding a
   sink only when its subscription signature (endpoint/secret/types/filters/active) changed
   and dropping sinks whose subscription was deactivated/deleted. New subscriptions start
   delivering within one pump — no daemon restart (open-closed, Law 5).
2. `WebhookSink` *is* a `BaseConsumer`: `name = sink:<id>`, `subscriptions` carry its
   interest, `handle(event)` builds the canonical JSON body (`event_envelope` +
   `canonical_bytes`, the *same* serialization SSE/WS use — Law 3), signs it, and POSTs
   with retry + exponential backoff. 5xx/408/429 retry; other 4xx are terminal. When every
   attempt is spent it raises `WebhookDeliveryError`.
3. The delivery bus runs at `max_handle_attempts=1` because the sink already owns retry, so
   one raise parks the event in the shared DLQ and **advances the cursor** — a dead endpoint
   never head-of-line-blocks the stream. Redrive with `redrive --consumer sink:<id>`.

## Wire contract (headers)
`X-LZT-Signature: sha256=<hex>` · `X-LZT-Event-Id` · `X-LZT-Event-Type` ·
`Idempotency-Key` (= event id, for receiver dedup). Body = deterministic, sorted-key JSON
of the event envelope; the receiver re-signs the **raw bytes** it received.

## Config (`EngineConfig`)
`webhook_max_attempts`, `webhook_backoff_base`, `webhook_backoff_max`, `webhook_timeout`,
`delivery_idle_poll`, `delivery_max_subscriptions`.

## Tested by
`tests/e2e/test_webhook_delivery_e2e.py` (poll→deliver, signature, retry→DLQ, 4xx-terminal,
runtime create/deactivate) and `tests/e2e/test_api_to_delivery_e2e.py` (management API
creates the subscription, engine delivers, body verifies under the returned secret).

See also: `../bus/_MODULE_AUTO.md` (CatchUpBus), `../web/services/_MODULE_AUTO.md`
(StreamService — the pull counterpart), `../codecs/_MODULE_AUTO.md` (shared serialization).
