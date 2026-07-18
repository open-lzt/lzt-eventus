# bus

Pull-forward event dispatch: each consumer (`BaseConsumer`) owns a `BaseCursorStore`
cursor, the bus pulls `log.read_after(cursor)` and replays in seq order. Not
fire-and-forget pub/sub — it is resumable, replayable, zero-loss.

## Contracts
- **Within a consumer, dispatch is strictly sequential and seq-ordered.** One worker
  per consumer, one `for`-loop over events; the cursor is committed after *every*
  event (handled, skipped, or parked). Never parallelise inside a consumer — it breaks
  ordering and the cursor invariant.
- **Across consumers, dispatch is concurrent.** `run()` runs one supervised worker per
  consumer (`_workers`, keyed by `consumer.name`). Consumers are independent — separate
  cursors, separate DLQ keys, read-only log — so a slow consumer never head-of-line-
  blocks a fast sibling.
- **`pump_once()` is sequential and deterministic** — for `drain_once` / `--dry-run`
  / tests. The concurrent path is `run()` only.
- **Membership is dynamic.** `register` / `unregister` (and `notify_membership` for a
  `consumer_provider`) flip `_membership_changed`; the supervisor reconciles live
  workers. A late consumer replays the whole log from seq 0.
- **Graceful drain on stop.** `run()` sets every worker's stop+wake and awaits them in
  `finally` before returning — no leaked tasks.
- **Poison events park, they don't block.** A `handle()` that fails past
  `max_handle_attempts` is parked in the DLQ and the cursor still advances (A5/D24).

## Gotchas
- `notify()` wakes **all** consumer workers (new events appended); `notify_membership()`
  triggers a **reconcile** (the live set changed). Don't confuse them.
- Edge-trigger order in `_run_worker`: `wake.clear()` happens BEFORE the log read, so
  an append+`notify()` racing a "0 events" read is never lost — the next wait returns
  immediately and re-pumps.
- `bus_max_concurrent_consumers` (config; `0` = unbounded) is a **bulkhead** — it bounds
  how many workers pump simultaneously, holding the semaphore per batch, not per event.

## ❌ Don't
- Don't add concurrency *inside* `_pump_consumer` — ordering/cursor invariant dies.
- Don't share one `consumer.name` across two consumers — workers and cursors collide.

## See also
- `_MODULE_AUTO.md` — signatures.
- Sources that feed the log: `../sources/`. Cursor store: `../cursor/`. DLQ: `dlq.py`.
