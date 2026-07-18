"""`RotatingSource` — round-robins per-account poll units on one cadence loop.

Wraps an ordered sequence of per-account poll units (e.g. one `RatingSource` per
Lolzteam account/token) and, on each `poll_once()` tick, invokes exactly
`accounts_per_tick` unit(s) starting from an internal cursor, wrapping around.
Reuses `BaseSource.run()`'s cadence loop unchanged — "every 5s, next account" is
just `cadence=5.0` on the `RotatingSource` itself, not per-unit.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from lzt_eventus.errors import EmptySourceUnits, InvalidAccountsPerTick
from lzt_eventus.lib.clock import Clock, RealClock
from lzt_eventus.sources.base import BaseSource


@runtime_checkable
class SourceUnit(Protocol):
    """Narrow shape a rotation member must satisfy — one account's poll step."""

    async def poll_once(self) -> int: ...


class RotatingSource(BaseSource):
    def __init__(
        self,
        *,
        units: Sequence[SourceUnit],
        accounts_per_tick: int = 1,
        min_cadence: float,
        max_cadence: float,
        cadence: float,
        clock: Clock | None = None,
    ) -> None:
        if not units:
            raise EmptySourceUnits()
        if accounts_per_tick <= 0:
            raise InvalidAccountsPerTick(accounts_per_tick)
        super().__init__(
            min_cadence=min_cadence,
            max_cadence=max_cadence,
            cadence=cadence,
            clock=clock or RealClock(),
        )
        self.name = "source:rotation"
        self._units = tuple(units)
        self._accounts_per_tick = accounts_per_tick
        self._cursor = 0

    async def poll_once(self) -> int:
        n = len(self._units)
        emitted = 0
        for offset in range(self._accounts_per_tick):
            unit = self._units[(self._cursor + offset) % n]
            emitted += await unit.poll_once()
        self._cursor = (self._cursor + self._accounts_per_tick) % n
        return emitted
