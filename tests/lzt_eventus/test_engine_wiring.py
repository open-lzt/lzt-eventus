"""`EventEngine` construction-time source wiring — the rating-rotation contract.

3+ `rating_clients` must collapse into exactly one `RotatingSource` registered
under the name `"rating-rotation"` (not one source per account) — see plan
decision #10 / task `T6-rating-rotation-wiring`.
"""

from __future__ import annotations

from pylzt.methods.users import GetSelfProfile
from pylzt.models.profile import Profile
from pydantic import SecretStr

from eventus_fakes import build_fake_engine
from lzt_eventus.config import EngineConfig
from lzt_eventus.sources.rotation import RotatingSource


class FakeRatingClient:
    """Stub `Client.execute` — only `GetSelfProfile` is ever exercised here."""

    def __init__(self, user_id: int) -> None:
        self._user_id = user_id

    async def execute(self, method: GetSelfProfile) -> Profile:
        return Profile(
            user_id=self._user_id,
            username="tester",
            user_like_count=0,
            user_dislike_count=0,
        )

    async def aclose(self) -> None:
        return None


def _config() -> EngineConfig:
    return EngineConfig(categories=[], tokens=[SecretStr("x")])


# Event-source sources (T4-engine-wiring) are always constructed by EventEngine
# alongside the catalog/rating ones; these assertions only care about the
# *last* source registered (the rating-rotation contract this file documents).
_EVENT_SOURCE_NAMES = (
    "source:payments:x",
    "source:notifications",
    "source:conversations",
    "source:guarantee",
)


def test_three_rating_clients_collapse_into_one_rotation_poller() -> None:
    accounts = [FakeRatingClient(1), FakeRatingClient(2), FakeRatingClient(3)]

    engine = build_fake_engine(
        _config(),
        client=accounts[0],  # type: ignore[arg-type]
        consumers=[],
        rating_clients=accounts,  # type: ignore[arg-type]
    )

    assert engine.source_names == (*_EVENT_SOURCE_NAMES, "rating-rotation")
    sources = engine._source_manager.sources  # whitebox wiring assertion
    assert len(sources) == 5
    assert isinstance(sources[-1], RotatingSource)


def test_single_rating_client_registers_a_plain_rating_poller_no_rotation_wrapper() -> None:
    accounts = [FakeRatingClient(1)]

    engine = build_fake_engine(
        _config(),
        client=accounts[0],  # type: ignore[arg-type]
        consumers=[],
        rating_clients=accounts,  # type: ignore[arg-type]
    )

    assert engine.source_names == (*_EVENT_SOURCE_NAMES, "source:rating")


def test_no_rating_clients_registers_no_rating_poller() -> None:
    engine = build_fake_engine(
        _config(),
        client=FakeRatingClient(1),  # type: ignore[arg-type]
        consumers=[],
    )

    assert engine.source_names == _EVENT_SOURCE_NAMES
