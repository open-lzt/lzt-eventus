# Event engine

<p align="right"><b>English</b> · <a href="event-engine.md">Русский</a></p>

The optional `event_engine` package turns the catalog into a **durable, replayable event
stream**: it polls each category, diffs snapshots, writes facts to an append-only log, and
dispatches them to your subscribers by a per-consumer cursor (poll → diff → log →
catch-up bus). A subscriber that registers late replays the whole log from seq 0 with zero
gaps; a handler that keeps failing is parked in a dead-letter queue instead of blocking the
stream.

Install the runtime extra for the durable (Postgres/Redis) backend:

```bash
pip install "lzt-eventus[engine] @ git+https://github.com/open-lzt/lzt-eventus.git"
```

## The events

| Event | Fields | `EventType` |
|---|---|---|
| `NewLotAppeared` | `lot` | `NEW_LOT` |
| `PriceDropped` | `old_price`, `new_price`, `lot` | `PRICE_DROPPED` |
| `LotUpdated` | `lot`, `changed` | `LOT_UPDATED` |
| `LotDisappeared` | `reason`, `confidence` | `LOT_DISAPPEARED` |
| `SnapshotInitialized` | `category`, `lot_count` | `SNAPSHOT_INITIALIZED` |

On cold start you get **one** `SnapshotInitialized` per category (never a flood of
per-lot events); incremental diffs then emit the rest. `LotDisappeared` carries a
`Confidence` (`NORMAL` / `LOW`) so you can tell a confirmed sale from a guess.

## Subscribe: a `BaseModule`

A subscriber declares which event types it wants and handles them:

```python
from lzt_eventus.plugins.module import BaseModule, BaseSubscription
from lzt_eventus.events.base import DomainEvent, EventType


class DealWatcher(BaseModule):
    name = "deals"  # unique — keys this consumer's cursor

    def __init__(self) -> None:
        self.subscriptions = [
            BaseSubscription(event_types=frozenset({EventType.PRICE_DROPPED}))
        ]

    async def handle(self, event: DomainEvent) -> None:
        # dispatch is sequential and seq-ordered per module; safe to keep local state
        print("price dropped:", event)
```

`BaseSubscription.filters` narrows further by payload (`all(payload[k] == v)`) — every
`NewLotAppeared`/`PriceDropped`/`LotUpdated`/`LotDisappeared`/`SnapshotInitialized` event
carries `category` in its payload, so one subscriber can watch a single category while
`config.categories` polls several:

```python
BaseSubscription(
    event_types=frozenset({EventType.PRICE_DROPPED}),
    filters={"category": "steam"},
)
```

## Subscribe: a decorator `EventRouter`

For many handlers over many event types, a router reads more naturally — same contract,
registered the same way:

```python
from lzt_eventus.plugins.router import EventRouter
from lzt_eventus.events.base import EventType


router = EventRouter(name="deals")


@router.on(EventType.PRICE_DROPPED)
async def on_drop(event) -> None:
    ...


@router.on(EventType.NEW_LOT, EventType.LOT_DISAPPEARED)
async def on_churn(event) -> None:
    ...
```

## Build and run

`build_memory` wires the in-process pipeline (great for a demo, a test, or a single-node
run); `build` wires the durable Postgres/Redis stores for the real daemon.

```python
from lzt_eventus.config import EngineConfig
from lzt_eventus.engine import EventEngine
from pylzt import Client, Category


config = EngineConfig(tokens=["<token>"], categories=[Category.STEAM])
client = Client(tokens=config.tokens)

engine = EventEngine.build_memory(config, client=client, modules=[DealWatcher()])
await engine.run()  # takes the lease, supervises pollers + bus, drains on stop
```

`engine.run()` blocks, supervising the poller fleet and the dispatch bus under one task
group. Stop it gracefully from elsewhere with `engine.request_stop()` (or Ctrl-C).

For a single deterministic tick (tests / `--dry-run`) use `drain_once()` — one poll of
every category plus one bus pump:

```python
await engine.drain_once()
```

## Configuration

`EngineConfig` is loaded from the environment (`LZT_*` prefix, `.env` supported) so no
secret lives in code:

```bash
LZT_TOKENS=["tok1","tok2"]
LZT_DATABASE_URL=postgresql://lzt:lzt@localhost:5432/lzt_core
LZT_CATEGORIES=["steam","discord"]
LZT_DEFAULT_CADENCE=30
LZT_BUS_MAX_CONCURRENT_MODULES=8      # bulkhead: max modules dispatching at once
```

```python
config = EngineConfig()               # reads the environment
```

Key knobs: `categories`, `default_cadence` / `min_cadence` / `max_cadence` (adaptive poll
spacing), `disappear_polls` (confirm-poll passes before a disappearance is declared),
`max_handle_attempts` (retries before a poison event is dead-lettered),
`bus_max_concurrent_modules` (cross-module dispatch bulkhead).

## Runtime changes (no restart)

Sources and subscribers can be added/dropped while the engine runs:

```python
engine.add_module(DealWatcher())      # picked up on the next bus pump
engine.remove_module("deals")         # cursor stays committed → safe resume if re-added
engine.add_poller(my_poller)          # supervisor starts its task immediately
engine.remove_poller("seller-rating") # stops that task gracefully
```

Infra singletons (`client`, stores, config, lease, clock) are construction-time only —
hot-swapping the log/cursor mid-pump would strand cursors. To change those, stop and
rebuild.

## Going further

Adding a new event type, a new event source (poller), an HTTP route, a store backend, a
webhook transport, or an in-process subscriber — all by subclassing, never by editing the
engine — is covered by the **`lzt-extending`** skill and [Extension points](../extending.en.md).
