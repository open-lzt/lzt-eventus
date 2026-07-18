"""`SourceManager` — live cadence updates and decorator registration."""

from __future__ import annotations

import pytest

from lzt_eventus.errors import SourceNotFound
from lzt_eventus.sources.base import BaseSource
from lzt_eventus.sources.manager import SourceManager


class _NoopSource(BaseSource):
    name = "noop"

    def __init__(self) -> None:
        super().__init__(min_cadence=1.0, max_cadence=60.0, cadence=30.0)

    async def poll_once(self) -> int:
        return 0


def test_update_source_cadence_retunes_live_poller() -> None:
    source = _NoopSource()
    manager = SourceManager([source])

    manager.update_source_cadence("noop", cadence=5.0)

    assert source._cadence == 5.0


def test_update_source_cadence_clamps_within_new_bounds() -> None:
    source = _NoopSource()
    manager = SourceManager([source])

    manager.update_source_cadence("noop", min_cadence=10.0, max_cadence=20.0)

    assert source._min == 10.0
    assert source._max == 20.0
    assert source._cadence == 20.0  # old 30.0 clamped down into the new [10, 20] window


def test_update_source_cadence_unknown_name_raises() -> None:
    manager = SourceManager([])

    with pytest.raises(SourceNotFound):
        manager.update_source_cadence("ghost", cadence=1.0)


def test_poller_decorator_registers_on_decoration() -> None:
    manager = SourceManager([])

    @manager.source(name="noop")
    def _build() -> BaseSource:
        return _NoopSource()

    assert manager.source_names == ("noop",)
