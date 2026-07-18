# examples/autobuy — demo built on the eventus devkit

Shows the intended autobuy wiring shape: a local `lzt_eventus` engine + management API
(one call, `lzt_eventus.devkit.local_eventus`) does the watching, filtering and durable
event log; this example only supplies the ~10-line business core and the buy side.
Per project direction the real autobuy product is a separate downstream repo built on
`lzt-core`/`lzt-eventus` — this is the wiring reference, not a shipped module.

## The ~10-line core (`dev.py::autobuy_from_new_lots`)

```python
results: list[PurchaseResult] = []
async with ManagementClient(server.base_url, api_key=server.api_key) as mgmt:
    sub = await mgmt.create_subscription(
        transport=SubscriptionTransport.POLLING,
        endpoint="autobuy-demo",
        event_types=[EventType.NEW_LOT],
        scope=CategoryScope(category=category),
    )
    while len(results) < limit:
        batch = await mgmt.poll_pending(sub.subscription_id, event_type=[EventType.NEW_LOT], limit=100)
        for event in batch.items:
            lot = event.data["lot"]
            if Decimal(str(lot["price"])) <= budget:
                results.append(await buyer.buy(ItemId(lot["item_id"]), Decimal(str(lot["price"]))))
        if batch.items:
            await mgmt.confirm_read(sub.subscription_id, up_to_seq=batch.next_seq)
        else:
            await asyncio.sleep(cadence)
```

Everything else in `dev.py` is boilerplate: CLI args, reading `LZT_TOKEN`, building the
market `Client`, and `local_eventus(...)` startup.

## Public surface
- `dev.py::autobuy_from_new_lots(server, buyer, category, *, budget, limit, cadence)`
  — the core loop above. Subscribes (POLLING, `CategoryScope`, `NEW_LOT`) against the
  local server, buys each new lot under `budget`, stops at `limit`.
- `buyer.py::Buyer.buy(item_id, price) -> PurchaseResult` — a single balance-diff-
  verified purchase (the one load-bearing gotcha, see below).
- `dev.py` — thin CLI. Token from `LZT_TOKEN` env var only.

## Requirements
- `pip install lzt-eventus-sdk` — the management SDK (`ManagementClient`, `EventType`,
  `CategoryScope`, `MarketCategory`, `SubscriptionTransport`) is a SEPARATE package, not
  vendored here. See the repo `README.md` for its API.
- The `engine` extra of this repo (`fastapi`, `uvicorn`) — pulled by `local_eventus`.

## Gotchas
- `purchasing_fast_buy`'s response model has known codegen drift against the live API
  (fields declared required the API omits, int fields that come back float) — a
  `pydantic.ValidationError` from that call does **not** mean the purchase failed; the
  POST already landed. `Buyer.buy()` confirms via balance-diff instead of trusting the
  parsed response.
- `config=EngineConfig(categories=[Category(...)])` must include the category you
  subscribe to — the engine only polls (and thus only produces events for) its
  configured catalogs.
- The demo shares one `Client` between the eventus poller and the buyer; a durable
  production deployment would run the engine as its own daemon (`python -m lzt_eventus`)
  and the buyer as a separate SDK consumer, not one in-process script.

## See also
- `src/lzt_eventus/devkit/_MODULE.md` — the `local_eventus` quickstart this builds on.
- `src/lzt_eventus/_MODULE.md` — the real engine behind the local server.
- `README.md` (repo root) — links `lzt-eventus-sdk` for the subscription API.
