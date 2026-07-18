"""`CategorySource` — turns catalog snapshots into persisted domain events.

One per configured category, all sharing the single `pylzt.Client` token pool
(never N independent sources — the anti-pattern this engine replaces). Each cycle:
page the category → diff against the durable baseline → resolve disappearances via
the confirm queue → append events + baseline in one atomic write → notify the bus.
Cold start runs in bootstrap mode (seed baseline + one `SnapshotInitialized`, no
per-lot flood). The disappearance miss-counter is durable (`last_seen.miss_count`).
"""

from __future__ import annotations

from pylzt.client import Client
from pylzt.models.lot import LotFilter
from pylzt.types import Category, ItemId

from lzt_eventus.baseline.store import BaseLastSeenStore, LastSeenBatch
from lzt_eventus.diff.differ import SnapshotDiffer
from lzt_eventus.diff.snapshot import BaselineEntry, Snapshot
from lzt_eventus.events.base import AggregateId, DomainEvent
from lzt_eventus.events.lot import LotDisappeared
from lzt_eventus.events.marker import SnapshotInitialized
from lzt_eventus.lib.clock import Clock, RealClock
from lzt_eventus.sources.base import BaseSource
from lzt_eventus.sources.confirm import ConfirmQueue
from lzt_eventus.transport import BaseTransport


class CategorySource(BaseSource):
    def __init__(
        self,
        *,
        client: Client,
        category: Category,
        transport: BaseTransport,
        last_seen: BaseLastSeenStore,
        confirm: ConfirmQueue,
        disappear_polls: int,
        poll_pages: int,
        per_page: int,
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
        self.name = f"source:{category.value}"
        self._client = client
        self._category = category
        self._transport = transport
        self._last_seen = last_seen
        self._confirm = confirm
        self._disappear_polls = disappear_polls
        self._differ = SnapshotDiffer()
        self._limit = poll_pages * per_page

    async def _fetch_snapshot(self) -> Snapshot:
        filter = LotFilter(category=self._category)
        lots = await self._client.market.list_lots(filter).collect(limit=self._limit)
        return Snapshot.from_lots(self._category, lots)

    async def poll_once(self) -> int:
        snapshot = await self._fetch_snapshot()
        epoch = await self._last_seen.get_poll_epoch(self._category)
        new_epoch = epoch + 1

        if not await self._last_seen.has_baseline(self._category):
            return await self._bootstrap(snapshot, new_epoch)

        prev = await self._last_seen.get_baseline(self._category)
        result = self._differ.diff(
            prev, snapshot, poll_epoch=new_epoch, occurred_at=self._clock.now()
        )
        events: list[DomainEvent] = list(result.events)

        upserts: dict[ItemId, BaselineEntry] = {
            item_id: BaselineEntry(price=lot.price, content_hash=lot.content_hash, miss_count=0)
            for item_id, lot in snapshot.lots.items()
        }
        drops, disappearance_events = await self._resolve_disappearances(
            result.absent_ids, prev, new_epoch, upserts
        )
        events.extend(disappearance_events)

        await self._transport.send(
            events,
            LastSeenBatch(
                category=self._category,
                poll_epoch=new_epoch,
                upserts=upserts,
                drops=frozenset(drops),
            ),
        )
        return len(events)

    async def _bootstrap(self, snapshot: Snapshot, epoch: int) -> int:
        """First poll of a category: seed the baseline, emit one marker, no flood."""
        upserts = {
            item_id: BaselineEntry(price=lot.price, content_hash=lot.content_hash, miss_count=0)
            for item_id, lot in snapshot.lots.items()
        }
        marker = SnapshotInitialized.build(
            aggregate_id=AggregateId(self._category.value),
            occurred_at=self._clock.now(),
            content_hash=f"bootstrap:{len(upserts)}",
            poll_epoch=epoch,
            category=self._category,
            lot_count=len(upserts),
            payload={"category": self._category.value},
        )
        await self._transport.send(
            [marker],
            LastSeenBatch(category=self._category, poll_epoch=epoch, upserts=upserts),
        )
        return 0  # zero *lot* events emitted — keeps cadence honest on cold start

    async def _resolve_disappearances(
        self,
        absent_ids: frozenset[ItemId],
        prev: dict[ItemId, BaselineEntry],
        epoch: int,
        upserts: dict[ItemId, BaselineEntry],
    ) -> tuple[set[ItemId], list[DomainEvent]]:
        """Bump the durable miss-counter; confirm + emit once at the threshold."""
        ripe: list[ItemId] = []
        drops: set[ItemId] = set()
        for item_id in absent_ids:
            base = prev[item_id]
            miss = base.miss_count + 1
            if miss >= self._disappear_polls:
                ripe.append(item_id)
            else:
                # Keep it in the baseline with the bumped counter (durable).
                upserts[item_id] = BaselineEntry(
                    price=base.price, content_hash=base.content_hash, miss_count=miss
                )

        events: list[DomainEvent] = []
        if ripe:
            confirmed = await self._confirm.resolve(ripe)
            for item_id in ripe:
                reason, confidence = confirmed.resolved[item_id]
                events.append(
                    LotDisappeared.build(
                        aggregate_id=AggregateId(str(int(item_id))),
                        occurred_at=self._clock.now(),
                        content_hash=f"gone:{int(item_id)}",
                        poll_epoch=epoch,
                        reason=reason,
                        confidence=confidence,
                        payload={"category": self._category.value},
                    )
                )
                drops.add(item_id)
        return drops, events
