"""`BaseCursorStore` — per-consumer `last_seq`, optimistically committed.

A consumer reads `log.read_after(cursor)` and commits its new position after
handling. Commit is optimistic (version-guarded) so two instances of the same
consumer cannot silently clobber each other's progress — the loser raises
`CursorConflict` (paired with the single-owner advisory lock for defense in depth).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CursorState:
    last_seq: int
    version: int


class BaseCursorStore(ABC):
    @abstractmethod
    async def get(self, consumer: str) -> CursorState:
        """Current position (last_seq=0, version=0 for an unknown consumer)."""

    @abstractmethod
    async def commit(self, consumer: str, seq: int, expected_version: int) -> None:
        """Advance to `seq` iff the stored version matches; else `CursorConflict`."""

    @abstractmethod
    async def delete(self, consumer: str) -> None:
        """Drop a consumer's cursor (subscription deactivation / retention)."""

    @abstractmethod
    async def watermark(self) -> int | None:
        """min(last_seq) across all cursors, or None if there are none."""
