from __future__ import annotations

from datetime import UTC, datetime

from pylzt.lib.clock import FakeClock
from pylzt.methods.users import GetSelfProfile
from pylzt.models.profile import Profile

from eventus_fakes import FakeEventLog, FakeLastSeenStore
from lzt_eventus.events.reputation import RatingChanged
from lzt_eventus.sources.rating import RatingSource
from lzt_eventus.transport import LogTransport

_USER_ID = 777


class FakeClient:
    """Stub `Client.execute` — only `GetSelfProfile` is ever passed in."""

    def __init__(self, likes: int, dislikes: int) -> None:
        self.likes = likes
        self.dislikes = dislikes

    async def execute(self, method: GetSelfProfile) -> Profile:
        return Profile(
            user_id=_USER_ID,
            username="tester",
            user_like_count=self.likes,
            user_dislike_count=self.dislikes,
        )


class FakeBus:
    def __init__(self) -> None:
        self.notified = 0

    def notify(self) -> None:
        self.notified += 1


def _source(
    client: FakeClient, log: FakeEventLog, last_seen: FakeLastSeenStore, bus: FakeBus
) -> RatingSource:
    return RatingSource(
        client=client,  # type: ignore[arg-type]
        transport=LogTransport(log, on_committed=bus.notify),
        last_seen=last_seen,
        min_cadence=1.0,
        max_cadence=60.0,
        cadence=5.0,
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
    )


async def test_first_poll_establishes_baseline_and_does_not_emit() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()
    client = FakeClient(likes=10, dislikes=2)

    emitted = await _source(client, log, last_seen, bus).poll_once()

    assert emitted == 0
    assert log._events == []
    assert bus.notified == 0


async def test_second_poll_with_changed_counts_emits_rating_changed_with_deltas() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()

    await _source(FakeClient(likes=10, dislikes=2), log, last_seen, bus).poll_once()
    emitted = await _source(FakeClient(likes=13, dislikes=3), log, last_seen, bus).poll_once()

    assert emitted == 1
    assert bus.notified == 1
    rating_events = [e for e in log._events if isinstance(e, RatingChanged)]
    assert len(rating_events) == 1
    event = rating_events[0]
    assert event.user_like_count == 13
    assert event.user_dislike_count == 3
    assert event.delta_likes == 3
    assert event.delta_dislikes == 1


async def test_third_poll_with_unchanged_counts_emits_nothing() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()

    await _source(FakeClient(likes=10, dislikes=2), log, last_seen, bus).poll_once()
    await _source(FakeClient(likes=13, dislikes=3), log, last_seen, bus).poll_once()
    emitted = await _source(FakeClient(likes=13, dislikes=3), log, last_seen, bus).poll_once()

    assert emitted == 0
    rating_events = [e for e in log._events if isinstance(e, RatingChanged)]
    assert len(rating_events) == 1  # no re-emit on unchanged counts


def _account_source(
    client: FakeClient,
    log: FakeEventLog,
    last_seen: FakeLastSeenStore,
    bus: FakeBus,
    *,
    account_alias: str | None,
) -> RatingSource:
    return RatingSource(
        client=client,  # type: ignore[arg-type]
        transport=LogTransport(log, on_committed=bus.notify),
        last_seen=last_seen,
        min_cadence=1.0,
        max_cadence=60.0,
        cadence=5.0,
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
        account_alias=account_alias,
    )


async def test_account_scoped_poller_tags_payload_with_account_alias() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()

    await _account_source(
        FakeClient(likes=10, dislikes=2), log, last_seen, bus, account_alias="acct-alias"
    ).poll_once()
    await _account_source(
        FakeClient(likes=11, dislikes=2), log, last_seen, bus, account_alias="acct-alias"
    ).poll_once()

    rating_events = [e for e in log._events if isinstance(e, RatingChanged)]
    assert len(rating_events) == 1
    assert rating_events[0].payload == {"account_alias": "acct-alias"}


async def test_engine_level_poller_has_no_account_alias_in_payload() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()

    await _source(FakeClient(likes=10, dislikes=2), log, last_seen, bus).poll_once()
    await _source(FakeClient(likes=11, dislikes=2), log, last_seen, bus).poll_once()

    rating_events = [e for e in log._events if isinstance(e, RatingChanged)]
    assert rating_events[0].payload == {}
