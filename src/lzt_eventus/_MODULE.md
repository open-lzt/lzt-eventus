# lzt_eventus/

Poll → domain events → durable replayable log → catch-up bus. See `_MODULE_AUTO.md`
for the generated per-submodule surface listing.

## Placement rules (layering audit, plan `.plans/eventus-layering-fixes/`)

- **`GuaranteeSeeder(BaseConsumer)` lives in `sources/guarantee.py`**, not in
  `consumers/`, even though it subclasses `BaseConsumer`. It's colocated by
  feature: it's a source-specific consumer that only exists to seed
  `GuaranteeWatcher`'s watch-list from `ItemPurchased` events, not a generic
  consumers/ primitive. Feature-colocation wins over generic-primitive-location
  when the class is source-specific.
- **Memory/Postgres coverage is symmetric across all stores.** `web/repos/*`
  (`SubscriptionRepo`, `TokenAccountRepo`) always shipped both `Memory*` and
  `Postgres*` implementations — their ABCs bind no engine (narrow, entity-scoped),
  so a memory double is cheap per entity. Core stores (`log/`, `cursor/`,
  `baseline/`, `bus/dlq`, `dedup/seen`) historically shipped Postgres-only in
  `src/`, with equivalent in-memory logic living only as test fakes
  (`tests/eventus_fakes.py`). That plan item promoted those fakes to first-class
  `Memory*` classes colocated with their ABCs, backing `EventEngine.build_memory()`
  (Law 29 — embedded, zero-infra runtime). Both store families now have
  Memory+Postgres coverage; the prior asymmetry was about *where the memory impl
  lived* (test-only vs first-class), not about which stores had one at all.
