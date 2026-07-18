"""Reusable batch iterator over `BaseEventLog.read_after` (library-design Law 28/22).

`CatchUpBus._pump_consumer` already paginates the log inline for its own dispatch loop;
this is the same page-then-advance-cursor mechanics extracted as a standalone async
generator, for anything else that wants to stream the log without re-deriving the
cursor math (an admin CLI, a replay/backfill script, an external consumer, a test).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from lzt_eventus.events.base import DomainEvent
from lzt_eventus.log.base import BaseEventLog


async def batches(
    log: BaseEventLog, *, after: int, limit: int = 500
) -> AsyncIterator[Sequence[DomainEvent]]:
    """Yield successive pages of events after `cursor`, one bounded page per iteration.

    `async for batch in batches(log, after=cursor): ...` — the consumer never sees
    `limit=`/cursor-advance mechanics, only typed pages. Stops on the first empty page.
    """
    cursor = after
    while True:
        page = await log.read_after(cursor, limit)
        if not page:
            return
        yield page
        cursor = page[-1].seq
