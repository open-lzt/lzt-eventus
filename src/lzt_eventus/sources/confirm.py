"""Disappearance confirm-poll — resolves `sold|delisted` with a bounded budget.

A burst of disappearances must not starve catalog polling (R5), so confirms run
through here: capped per cycle, drained via the wave-01 `/batch` (K confirms →
⌈K/batch_size⌉ requests), and overflow is deferred — degrading to
`unknown`/`low` rather than blocking. `get_lot` is a *general*-class request,
drawn from a config fraction of the general bucket.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pylzt.client import Client
from pylzt.errors import LztError
from pylzt.types import ItemId

from lzt_eventus.events.lot import Confidence, DisappearReason

# UNVERIFIED: the exact item_state literal for a sold lot. Until pinned from a
# recorded probe, a sold/paid-looking state maps to SOLD; everything else degrades.
_SOLD_STATES = frozenset({"sold", "paid", "purchased"})


def resolve_reason(item_state: str | None, *, found: bool) -> tuple[DisappearReason, Confidence]:
    if not found:
        return DisappearReason.UNKNOWN, Confidence.LOW  # likely sold/deleted, can't confirm
    if item_state == "closed":
        return DisappearReason.DELISTED, Confidence.NORMAL
    if item_state in _SOLD_STATES:
        return DisappearReason.SOLD, Confidence.NORMAL
    return DisappearReason.UNKNOWN, Confidence.LOW


@dataclass(frozen=True, slots=True)
class ConfirmResult:
    resolved: dict[ItemId, tuple[DisappearReason, Confidence]]
    deferred: tuple[ItemId, ...]


class ConfirmQueue:
    def __init__(self, client: Client, *, per_cycle_cap: int) -> None:
        self._client = client
        self._cap = per_cycle_cap

    async def resolve(self, item_ids: Sequence[ItemId]) -> ConfirmResult:
        to_check = list(item_ids[: self._cap])
        deferred = tuple(item_ids[self._cap :])
        resolved: dict[ItemId, tuple[DisappearReason, Confidence]] = {}
        if to_check:
            try:
                lots = await self._client.market.get_lots_batch(to_check)
            except LztError:
                lots = []
            states = {lot.item_id: lot.item_state for lot in lots}
            for item_id in to_check:
                if item_id in states:
                    resolved[item_id] = resolve_reason(states[item_id], found=True)
                else:
                    resolved[item_id] = resolve_reason(None, found=False)
        # Overflow degrades to unknown/low this cycle (re-tried next cycle upstream).
        for item_id in deferred:
            resolved[item_id] = (DisappearReason.UNKNOWN, Confidence.LOW)
        return ConfirmResult(resolved=resolved, deferred=deferred)
