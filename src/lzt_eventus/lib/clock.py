"""Engine clock — re-exports the SDK `Clock` so time is one concept repo-wide.

The engine injects this wherever time is read (`occurred_at`, poll cadence, the
`disappear_polls` miss-counter, the confirm-queue throttle) so the time-based
logic is unit-tested deterministically with a `FakeClock`.
"""

from __future__ import annotations

from pylzt.lib.clock import Clock, FakeClock, RealClock

__all__ = ["Clock", "FakeClock", "RealClock"]
