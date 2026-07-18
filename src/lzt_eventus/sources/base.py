"""`BaseSource` — a source that emits events on an adaptive cadence.

Subclasses implement one `poll_once()`; the base owns the run loop, the
`[min,max]` cadence clamp, and the self-tuning by measured `events_per_poll`
(D-CADENCE) — churny categories poll faster, quiet ones back off, never past the
token-bucket cap.
"""

from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod

import structlog

from lzt_eventus.lib.clock import Clock, RealClock

_log = structlog.get_logger("lzt_eventus.sources")


class BaseSource(ABC):
    name: str

    def __init__(
        self,
        *,
        min_cadence: float,
        max_cadence: float,
        cadence: float,
        clock: Clock | None = None,
    ) -> None:
        self._min = min_cadence
        self._max = max_cadence
        self._cadence = max(min_cadence, min(cadence, max_cadence))
        self._clock = clock or RealClock()

    @abstractmethod
    async def poll_once(self) -> int:
        """Run one poll cycle; return the number of events emitted."""

    def update_cadence(
        self,
        *,
        min_cadence: float | None = None,
        max_cadence: float | None = None,
        cadence: float | None = None,
    ) -> None:
        """Retune the live cadence bounds — the next `run()` iteration picks it up.

        No restart needed (library-design Law 28): an operator/admin seam can call this
        on a source that's already inside `SourceManager`'s supervised loop.
        """
        if min_cadence is not None:
            self._min = min_cadence
        if max_cadence is not None:
            self._max = max_cadence
        if cadence is not None:
            self._cadence = cadence
        self._cadence = max(self._min, min(self._cadence, self._max))

    def _retune(self, emitted: int) -> None:
        # More events → poll sooner (halve toward min); none → back off (1.5x to max).
        if emitted > 0:
            self._cadence = max(self._min, self._cadence / 2)
        else:
            self._cadence = min(self._max, self._cadence * 1.5)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                emitted = await self.poll_once()
                self._retune(emitted)
            except Exception:
                _log.exception("poll_failed", source=self.name)
                emitted = 0
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._cadence)
