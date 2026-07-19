"""Polling API over TestClient: subscription creation, type filter, read tracking."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from pydantic import SecretStr
from pylzt.types import Category

from eventus_fakes import fake_engine_handle
from lzt_eventus.baseline.store import LastSeenBatch
from lzt_eventus.config import EngineConfig
from lzt_eventus.events.base import AggregateId, DomainEvent, EventType, make_event_id
from lzt_eventus.web.main import build_app

ADMIN = "admin-secret-key"


def _event(event_type: EventType, agg: str, payload: dict[str, object]) -> DomainEvent:
    return DomainEvent(
        event_id=make_event_id(AggregateId(agg), event_type, agg, 0),
        aggregate_id=AggregateId(agg),
        occurred_at=datetime.now(UTC),
        content_hash=agg,
        payload=payload,
        _event_type=event_type,
    )


def _client() -> TestClient:
    cfg = EngineConfig(admin_api_key=SecretStr(ADMIN))
    handle = fake_engine_handle(cfg)

    async def _seed() -> None:
        await handle.event_log.append(
            [
                _event(EventType.NEW_LOT, "a1", {"title": "first"}),
                _event(EventType.PRICE_DROPPED, "a2", {"title": "skip"}),
                _event(EventType.NEW_LOT, "a3", {"title": "second"}),
            ],
            LastSeenBatch(category=Category.OTHER, poll_epoch=0),
        )

    asyncio.run(_seed())
    return TestClient(build_app(handle))


def _auth() -> dict[str, str]:
    return {"X-API-Key": ADMIN}


def _create_poll_sub(
    c: TestClient, name: str, event_types: list[str], *, backfill: bool = True
) -> str:
    r = c.post(
        "/subscriptions/create",
        json={
            "transport": "polling",
            "endpoint": name,
            "event_types": event_types,
            "backfill": backfill,
        },
        headers=_auth(),
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["secret"] is None and data["stream_token"] is None  # pull-only, no push secret
    return str(data["subscription_id"])


def test_polling_subscription_create_requires_admin_key() -> None:
    c = _client()
    r = c.post(
        "/subscriptions/create",
        json={"transport": "polling", "endpoint": "p1", "event_types": ["new_lot"]},
    )
    assert r.status_code == 401


def test_pending_requires_a_registered_subscription() -> None:
    c = _client()
    r = c.get("/events/pending", params={"subscription_id": "nope"}, headers=_auth())
    assert r.status_code == 404


def test_pending_rejects_non_polling_subscription() -> None:
    c = _client()
    sid = c.post(
        "/subscriptions/create",
        json={"transport": "sse", "endpoint": "client-1", "event_types": ["new_lot"]},
        headers=_auth(),
    ).json()["data"]["subscription_id"]
    r = c.get("/events/pending", params={"subscription_id": sid}, headers=_auth())
    assert r.status_code == 400
    assert r.json()["error"] == "not_a_polling_subscription"


def test_pending_rejects_unknown_event_type() -> None:
    c = _client()
    sid = _create_poll_sub(c, "source-1", ["new_lot"])
    r = c.get(
        "/events/pending", params={"subscription_id": sid, "event_type": "bogus"}, headers=_auth()
    )
    assert r.status_code == 400
    assert r.json()["error"] == "unknown_event_type"


def test_pending_rejects_limit_too_large() -> None:
    c = _client()
    sid = _create_poll_sub(c, "source-1", ["new_lot"])
    r = c.get("/events/pending", params={"subscription_id": sid, "limit": 999_999}, headers=_auth())
    assert r.status_code == 400
    assert r.json()["error"] == "limit_too_large"


def test_pending_rejects_non_integer_limit() -> None:
    c = _client()
    sid = _create_poll_sub(c, "source-1", ["new_lot"])
    r = c.get("/events/pending", params={"subscription_id": sid, "limit": "abc"}, headers=_auth())
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_limit"


def test_pending_filters_by_subscription_event_types() -> None:
    c = _client()
    sid = _create_poll_sub(c, "source-1", ["new_lot"])
    data = c.get("/events/pending", params={"subscription_id": sid}, headers=_auth()).json()
    assert [i["seq"] for i in data["items"]] == [1, 3]
    assert all(i["event_type"] == "new_lot" for i in data["items"])
    assert data["subscription_id"] == sid
    assert data["committed"] is False


def test_pending_query_event_type_narrows_further() -> None:
    c = _client()
    sid = _create_poll_sub(c, "source-1", ["new_lot", "price_dropped"])
    data = c.get(
        "/events/pending",
        params={"subscription_id": sid, "event_type": "price_dropped"},
        headers=_auth(),
    ).json()
    assert [i["seq"] for i in data["items"]] == [2]


def test_read_all_false_does_not_advance_cursor() -> None:
    c = _client()
    sid = _create_poll_sub(c, "source-1", ["new_lot", "price_dropped"])
    first = c.get("/events/pending", params={"subscription_id": sid}, headers=_auth()).json()
    second = c.get("/events/pending", params={"subscription_id": sid}, headers=_auth()).json()
    assert [i["seq"] for i in first["items"]] == [i["seq"] for i in second["items"]] == [1, 2, 3]
    assert second["last_read_seq"] == 0


def test_read_all_true_commits_the_batch() -> None:
    c = _client()
    sid = _create_poll_sub(c, "source-1", ["new_lot", "price_dropped"])
    first = c.get(
        "/events/pending", params={"subscription_id": sid, "read_all": True}, headers=_auth()
    ).json()
    assert first["committed"] is True
    second = c.get("/events/pending", params={"subscription_id": sid}, headers=_auth()).json()
    assert second["items"] == []
    assert second["last_read_seq"] == 3


def test_read_events_commits_explicitly() -> None:
    c = _client()
    sid = _create_poll_sub(c, "source-1", ["new_lot", "price_dropped"])
    c.get("/events/pending", params={"subscription_id": sid}, headers=_auth())
    r = c.post(
        "/events/read_events",
        json={"subscription_id": sid, "up_to_seq": 2},
        headers=_auth(),
    )
    assert r.status_code == 200
    assert r.json() == {"subscription_id": sid, "last_seq": 2}

    remaining = c.get("/events/pending", params={"subscription_id": sid}, headers=_auth()).json()
    assert [i["seq"] for i in remaining["items"]] == [3]


def test_read_events_stale_seq_is_noop() -> None:
    c = _client()
    sid = _create_poll_sub(c, "source-1", ["new_lot", "price_dropped"])
    c.post("/events/read_events", json={"subscription_id": sid, "up_to_seq": 3}, headers=_auth())
    r = c.post(
        "/events/read_events", json={"subscription_id": sid, "up_to_seq": 1}, headers=_auth()
    )
    assert r.status_code == 200
    assert r.json()["last_seq"] == 3


def test_two_polling_subscriptions_track_independent_cursors() -> None:
    c = _client()
    sid_a = _create_poll_sub(c, "source-a", ["new_lot"])
    sid_b = _create_poll_sub(c, "source-b", ["new_lot"])

    c.post("/events/read_events", json={"subscription_id": sid_a, "up_to_seq": 3}, headers=_auth())

    a = c.get("/events/pending", params={"subscription_id": sid_a}, headers=_auth()).json()
    b = c.get("/events/pending", params={"subscription_id": sid_b}, headers=_auth()).json()
    assert a["items"] == []  # already confirmed on sub A
    assert [i["seq"] for i in b["items"]] == [1, 3]  # sub B untouched
