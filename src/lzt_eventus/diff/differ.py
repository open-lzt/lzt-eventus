"""`SnapshotDiffer` — pure, sans-I/O. The single source of catalog events.

Given the durable baseline and the current poll, it emits NewLot / PriceDropped /
LotUpdated for *present* lots and reports which ids are *absent*. It deliberately
does NOT decide `LotDisappeared`: that needs the durable miss-counter (N
consecutive polls) and a confirm `get_lot`, both of which are I/O the source owns.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from pylzt.models.lot import Lot
from pylzt.types import ItemId

from lzt_eventus.diff.snapshot import BaselineEntry, Snapshot
from lzt_eventus.events.base import AggregateId, DomainEvent
from lzt_eventus.events.lot import LotUpdated, NewLotAppeared, PriceDropped


@dataclass(frozen=True, slots=True)
class DiffResult:
    events: list[DomainEvent] = field(default_factory=list)
    present_ids: frozenset[ItemId] = field(default_factory=frozenset)
    absent_ids: frozenset[ItemId] = field(default_factory=frozenset)


def _category_payload(lot: Lot) -> dict[str, object]:
    """`filters={"category": "steam"}` on a subscription matches this."""
    return {"category": lot.category.value}


class SnapshotDiffer:
    def diff(
        self,
        prev: Mapping[ItemId, BaselineEntry],
        curr: Snapshot,
        *,
        poll_epoch: int,
        occurred_at: datetime,
    ) -> DiffResult:
        events: list[DomainEvent] = []
        for item_id, lot in curr.lots.items():
            agg = AggregateId(str(int(item_id)))
            base = prev.get(item_id)
            if base is None:
                events.append(
                    NewLotAppeared.build(
                        aggregate_id=agg,
                        occurred_at=occurred_at,
                        content_hash=lot.content_hash,
                        poll_epoch=poll_epoch,
                        lot=lot,
                        payload=_category_payload(lot),
                    )
                )
            elif lot.price < base.price:
                events.append(
                    PriceDropped.build(
                        aggregate_id=agg,
                        occurred_at=occurred_at,
                        content_hash=lot.content_hash,
                        poll_epoch=poll_epoch,
                        old_price=base.price,
                        new_price=lot.price,
                        lot=lot,
                        payload=_category_payload(lot),
                    )
                )
            elif lot.content_hash != base.content_hash:
                # Same-or-higher price but a meaningful field changed.
                events.append(
                    LotUpdated.build(
                        aggregate_id=agg,
                        occurred_at=occurred_at,
                        content_hash=lot.content_hash,
                        poll_epoch=poll_epoch,
                        lot=lot,
                        changed=frozenset({"content"}),
                        payload=_category_payload(lot),
                    )
                )
        present = curr.ids()
        absent = frozenset(prev) - present
        return DiffResult(events=events, present_ids=present, absent_ids=absent)
