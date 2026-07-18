import math

import pytest

from lzt_eventus.errors import EmptySourceUnits, InvalidAccountsPerTick
from lzt_eventus.sources.rotation import RotatingSource


class FakeUnit:
    def __init__(self, name: str, emits: int = 1) -> None:
        self.name = name
        self._emits = emits
        self.calls = 0

    async def poll_once(self) -> int:
        self.calls += 1
        return self._emits


def _rotator(units: list[FakeUnit], accounts_per_tick: int = 1) -> RotatingSource:
    return RotatingSource(
        units=units,
        accounts_per_tick=accounts_per_tick,
        min_cadence=1.0,
        max_cadence=10.0,
        cadence=5.0,
    )


async def test_strict_round_robin_order_over_two_full_cycles() -> None:
    units = [FakeUnit("a"), FakeUnit("b"), FakeUnit("c")]
    source = _rotator(units)

    for _ in range(2 * len(units)):
        await source.poll_once()

    assert [u.calls for u in units] == [2, 2, 2]


async def test_no_skip_no_double_hit_within_one_cycle() -> None:
    units = [FakeUnit("a"), FakeUnit("b"), FakeUnit("c")]
    source = _rotator(units)

    visited: list[str] = []
    for _ in range(len(units)):
        cursor_before = source._cursor
        await source.poll_once()
        visited.append(units[cursor_before].name)

    assert visited == ["a", "b", "c"]


async def test_returns_sum_of_emitted_counts() -> None:
    units = [FakeUnit("a", emits=3), FakeUnit("b", emits=4)]
    source = _rotator(units, accounts_per_tick=2)

    assert await source.poll_once() == 7


async def test_accounts_per_tick_two_with_three_units_wraps_around() -> None:
    units = [FakeUnit("a"), FakeUnit("b"), FakeUnit("c")]
    source = _rotator(units, accounts_per_tick=2)
    ticks = math.ceil(len(units) / 2)

    order: list[tuple[str, ...]] = []
    for _ in range(ticks):
        cursor_before = source._cursor
        touched = tuple(units[(cursor_before + offset) % len(units)].name for offset in range(2))
        await source.poll_once()
        order.append(touched)

    # tick 1: (a, b) — tick 2: (c, a) — wraps around, full cycle covers every unit.
    assert order == [("a", "b"), ("c", "a")]
    assert [u.calls for u in units] == [2, 1, 1]


async def test_empty_units_raises_at_construction() -> None:
    with pytest.raises(EmptySourceUnits):
        _rotator([])


@pytest.mark.parametrize("accounts_per_tick", [0, -1])
async def test_non_positive_accounts_per_tick_raises_at_construction(
    accounts_per_tick: int,
) -> None:
    with pytest.raises(InvalidAccountsPerTick):
        _rotator([FakeUnit("a")], accounts_per_tick=accounts_per_tick)
