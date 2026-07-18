"""`InMemoryRepo` — the dict-backed CRUD every entity's in-memory (devkit) repo shares.

Each entity's repo has two backends: a durable Postgres one (SQLAlchemy) and an
in-memory one for the zero-infra devkit / test path. The in-memory ordering and
filtering — a `dict[TId, TModel]` read back sorted by `created_at` and filtered by
`active` — is identical across entities, so it lives here once. An entity's memory
repo mixes this in and adds only its own `get`/`add`/`replace` (whose parameter names
must match the entity ABC) plus any entity-specific finders.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol


class _Lifecycle(Protocol):
    # Read-only members: the domain models are frozen dataclasses (mutated via
    # `dataclasses.replace`), so the protocol must not demand settable attributes.
    @property
    def created_at(self) -> datetime: ...
    @property
    def active(self) -> bool: ...


class InMemoryRepo[TModel: _Lifecycle, TId]:
    def __init__(self) -> None:
        self._items: dict[TId, TModel] = {}

    def _filtered(self, *, active_only: bool) -> list[TModel]:
        rows = sorted(self._items.values(), key=lambda m: m.created_at)
        return [m for m in rows if m.active] if active_only else rows

    async def list(self, *, limit: int, offset: int, active_only: bool = False) -> Sequence[TModel]:
        return self._filtered(active_only=active_only)[offset : offset + limit]

    async def count(self, *, active_only: bool = False) -> int:
        return len(self._filtered(active_only=active_only))
