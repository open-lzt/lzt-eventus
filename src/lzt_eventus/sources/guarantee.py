"""`GuaranteeWatcher` — periodic re-check of purchased items nearing guarantee expiry.

Dual role, two classes: `GuaranteeSeeder` (a `BaseConsumer`) adds an item to the
watch-list the moment `ItemPurchased` clears the bus; `GuaranteeWatcher` (a
`BaseSource`) re-fetches each watched item on its own cadence and fires
`GuaranteeExpiring` once per crossed threshold (24h/6h/1h). Both share one
`BaseLastSeenStore` bucket (`~/.claude/rules/patterns.md` Discover-before-write —
no new storage ABC) with item keys offset by `_ITEM_KEY_OFFSET` so this bucket
can never collide with a real `CategorySource`'s baseline rows in the same table.
`Lot.guarantee` is an opaque upstream `str` (`pylzt.models._parse`, wire format
UNVERIFIED) — anything that isn't ISO-8601 is treated as "nothing to watch", not
an error, since a foreign/relative format is expected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

import structlog
from pylzt.client import Client
from pylzt.errors import NotFound
from pylzt.types import Category, ItemId

from lzt_eventus.baseline.store import BaseLastSeenStore, LastSeenBatch
from lzt_eventus.consumers.consumer import BaseConsumer, BaseSubscription
from lzt_eventus.diff.snapshot import BaselineEntry
from lzt_eventus.events.account import GuaranteeExpiring
from lzt_eventus.events.base import AggregateId, DomainEvent, EventType
from lzt_eventus.lib.clock import Clock, RealClock
from lzt_eventus.sources.base import BaseSource
from lzt_eventus.transport import BaseTransport

_log = structlog.get_logger("lzt_eventus.sources.guarantee")

# Reserved key space: real lzt.market item_ids never reach this magnitude, so
# offsetting by it lets the watch-list share `BaseLastSeenStore`'s physical
# table with real `CategorySource` baselines (same category bucket) without
# ever reading or overwriting one of their rows.
_ITEM_KEY_OFFSET: Final[int] = 10**15
_BUCKET: Final[Category] = Category.OTHER

# Ascending duration — smallest (most urgent) checked first so exactly one
# event fires per tick even when several thresholds are crossed at once.
_THRESHOLDS: Final[tuple[tuple[str, timedelta], ...]] = (
    ("1h", timedelta(hours=1)),
    ("6h", timedelta(hours=6)),
    ("24h", timedelta(hours=24)),
)


@dataclass(frozen=True, slots=True)
class _WatchRecord:
    guarantee_end: datetime
    fired: frozenset[str]


def _parse_guarantee_end(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _store_key(item_id: int) -> ItemId:
    return ItemId(item_id + _ITEM_KEY_OFFSET)


def _encode(record: _WatchRecord) -> str:
    return json.dumps(
        {"guarantee_end": record.guarantee_end.isoformat(), "fired": sorted(record.fired)}
    )


def _decode(raw: str) -> _WatchRecord | None:
    try:
        data = json.loads(raw)
        return _WatchRecord(
            guarantee_end=datetime.fromisoformat(data["guarantee_end"]),
            fired=frozenset(data["fired"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None  # foreign row sharing the bucket (or corrupt) — not ours to touch


def _due_thresholds(remaining: timedelta, fired: frozenset[str]) -> list[str]:
    """Thresholds newly crossed this tick, nearest-expiry first."""
    return [label for label, window in _THRESHOLDS if label not in fired and remaining <= window]


class GuaranteeSeeder(BaseConsumer):
    """Bus consumer: seeds the watch-list the moment an item is purchased."""

    name = "guarantee_seeder"

    def __init__(self, *, client: Client, last_seen: BaseLastSeenStore) -> None:
        self.subscriptions = [
            BaseSubscription[DomainEvent](event_types=frozenset({EventType.ITEM_PURCHASED}))
        ]
        self._client = client
        self._last_seen = last_seen

    async def handle(self, event: DomainEvent) -> None:
        try:
            item_id = int(event.aggregate_id)
        except ValueError:
            _log.warning("guarantee_seed_bad_aggregate_id", aggregate_id=event.aggregate_id)
            return

        try:
            lot = await self._client.market.get_lot(ItemId(item_id))
        except NotFound:
            _log.warning("guarantee_seed_lot_not_found", item_id=item_id)
            return

        guarantee_end = _parse_guarantee_end(lot.guarantee)
        if guarantee_end is None:
            _log.debug("guarantee_seed_unparseable", item_id=item_id, guarantee=lot.guarantee)
            return

        epoch = await self._last_seen.get_poll_epoch(_BUCKET) + 1
        record = _WatchRecord(guarantee_end=guarantee_end, fired=frozenset())
        entry = BaselineEntry(price=Decimal(0), content_hash=_encode(record))
        await self._last_seen.apply(
            LastSeenBatch(category=_BUCKET, poll_epoch=epoch, upserts={_store_key(item_id): entry})
        )


class GuaranteeWatcher(BaseSource):
    """Source: re-checks watched items and emits `GuaranteeExpiring` at 24h/6h/1h."""

    def __init__(
        self,
        *,
        client: Client,
        transport: BaseTransport,
        last_seen: BaseLastSeenStore,
        min_cadence: float,
        max_cadence: float,
        cadence: float,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(
            min_cadence=min_cadence,
            max_cadence=max_cadence,
            cadence=cadence,
            clock=clock or RealClock(),
        )
        self.name = "source:guarantee"
        self._client = client
        self._transport = transport
        self._last_seen = last_seen
        self._clock = clock or RealClock()

    async def _watched_items(self) -> dict[int, _WatchRecord]:
        baseline = await self._last_seen.get_baseline(_BUCKET)
        watched: dict[int, _WatchRecord] = {}
        for stored_id, entry in baseline.items():
            if int(stored_id) < _ITEM_KEY_OFFSET:
                continue  # a real CategorySource's row sharing this bucket
            record = _decode(entry.content_hash)
            if record is not None:
                watched[int(stored_id) - _ITEM_KEY_OFFSET] = record
        return watched

    async def poll_once(self) -> int:
        watched = await self._watched_items()
        if not watched:
            return 0

        now = self._clock.now()
        epoch = await self._last_seen.get_poll_epoch(_BUCKET) + 1
        events: list[DomainEvent] = []
        upserts: dict[ItemId, BaselineEntry] = {}
        drops: set[ItemId] = set()

        for item_id, record in watched.items():
            try:
                lot = await self._client.market.get_lot(ItemId(item_id))
            except NotFound:
                drops.add(_store_key(item_id))
                continue

            guarantee_end = _parse_guarantee_end(lot.guarantee) or record.guarantee_end
            if guarantee_end <= now:
                drops.add(_store_key(item_id))
                continue

            due = _due_thresholds(guarantee_end - now, record.fired)
            if due:
                events.append(
                    GuaranteeExpiring.build(
                        aggregate_id=AggregateId(str(item_id)),
                        occurred_at=now,
                        content_hash=f"guarantee:{item_id}:{due[0]}",
                        poll_epoch=epoch,
                        item_id=item_id,
                        guarantee_end=guarantee_end,
                    )
                )
            fired = record.fired | frozenset(due)
            upserts[_store_key(item_id)] = BaselineEntry(
                price=Decimal(0),
                content_hash=_encode(_WatchRecord(guarantee_end=guarantee_end, fired=fired)),
            )

        baseline = LastSeenBatch(
            category=_BUCKET, poll_epoch=epoch, upserts=upserts, drops=frozenset(drops)
        )
        await self._transport.send(events, baseline)
        return len(events)
