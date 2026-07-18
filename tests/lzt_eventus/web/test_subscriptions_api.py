"""Management API over TestClient: admin-key gate, CRUD, redaction, tokens."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from eventus_fakes import fake_engine_handle
from lzt_eventus.config import EngineConfig
from lzt_eventus.web.main import build_app

ADMIN = "admin-secret-key"


def _client() -> TestClient:
    cfg = EngineConfig(admin_api_key=SecretStr(ADMIN))
    return TestClient(build_app(fake_engine_handle(cfg)))


def _auth() -> dict[str, str]:
    return {"X-API-Key": ADMIN}


def test_management_requires_admin_key() -> None:
    c = _client()
    assert c.get("/subscriptions/list").status_code == 401
    assert c.get("/subscriptions/list", headers={"X-API-Key": "wrong"}).status_code == 401


def test_webhook_create_returns_secret_once_then_redacts() -> None:
    c = _client()
    r = c.post(
        "/subscriptions/create",
        json={
            "transport": "webhook",
            "endpoint": "https://example.com/webhook",
            "event_types": ["new_lot"],
        },
        headers=_auth(),
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["secret"] and data["stream_token"] is None  # webhook → HMAC secret
    sid = data["subscription_id"]

    listed = c.get("/subscriptions/list", headers=_auth()).json()["items"]
    assert listed[0]["secret"] is None  # redacted on list
    assert listed[0]["subscription_id"] == sid

    got = c.get("/subscriptions/get", params={"subscription_id": sid}, headers=_auth())
    assert got.json()["data"]["subscription_id"] == sid

    deact = c.post("/subscriptions/deactivate", json={"subscription_id": sid}, headers=_auth())
    assert deact.json()["ok"] is True
    active = c.get("/subscriptions/list", params={"active_only": True}, headers=_auth())
    assert active.json()["total"] == 0


def test_sse_create_returns_stream_token_not_secret() -> None:
    c = _client()
    r = c.post(
        "/subscriptions/create",
        json={"transport": "sse", "endpoint": "client-1", "event_types": ["new_lot"]},
        headers=_auth(),
    )
    data = r.json()["data"]
    assert data["stream_token"] and data["secret"] is None


def test_unknown_event_type_rejected() -> None:
    c = _client()
    r = c.post(
        "/subscriptions/create",
        json={"transport": "webhook", "endpoint": "https://h", "event_types": ["bogus"]},
        headers=_auth(),
    )
    assert r.status_code == 400
    assert r.json()["error"] == "unknown_event_type"


def test_omitted_ctx_defaults_by_transport() -> None:
    c = _client()
    r = c.post(
        "/subscriptions/create",
        json={"transport": "polling", "endpoint": "n/a", "event_types": ["new_lot"]},
        headers=_auth(),
    )
    assert r.json()["data"]["ctx"] == {"kind": "polling", "poll_delay_seconds": 0.0}


def test_explicit_polling_ctx_round_trips() -> None:
    c = _client()
    r = c.post(
        "/subscriptions/create",
        json={
            "transport": "polling",
            "endpoint": "n/a",
            "event_types": ["new_lot"],
            "ctx": {"kind": "polling", "poll_delay_seconds": 5.0},
        },
        headers=_auth(),
    )
    assert r.json()["data"]["ctx"]["poll_delay_seconds"] == 5.0


def test_ctx_kind_mismatch_with_transport_rejected() -> None:
    c = _client()
    r = c.post(
        "/subscriptions/create",
        json={
            "transport": "webhook",
            "endpoint": "https://h",
            "event_types": ["new_lot"],
            "ctx": {"kind": "polling", "poll_delay_seconds": 1.0},
        },
        headers=_auth(),
    )
    assert r.status_code == 400
    assert r.json()["error"] == "subscription_ctx_mismatch"


def test_omitted_scope_defaults_to_none() -> None:
    c = _client()
    r = c.post(
        "/subscriptions/create",
        json={"transport": "polling", "endpoint": "n/a", "event_types": ["new_lot"]},
        headers=_auth(),
    )
    assert r.json()["data"]["scope"] == {"kind": "none"}


def test_category_scope_round_trips() -> None:
    c = _client()
    r = c.post(
        "/subscriptions/create",
        json={
            "transport": "polling",
            "endpoint": "n/a",
            "event_types": ["new_lot", "price_dropped"],
            "scope": {"kind": "category", "category": "steam"},
        },
        headers=_auth(),
    )
    assert r.json()["data"]["scope"] == {"kind": "category", "category": "steam"}


def test_category_scope_rejected_for_non_catalog_event_type() -> None:
    c = _client()
    r = c.post(
        "/subscriptions/create",
        json={
            "transport": "webhook",
            "endpoint": "https://h",
            "event_types": ["rating_changed"],
            "scope": {"kind": "category", "category": "steam"},
        },
        headers=_auth(),
    )
    assert r.status_code == 400
    assert r.json()["error"] == "subscription_scope_mismatch"


def test_unsafe_webhook_endpoint_rejected() -> None:
    c = _client()
    r = c.post(
        "/subscriptions/create",
        json={
            "transport": "webhook",
            "endpoint": "http://127.0.0.1:8000/webhook",
            "event_types": ["new_lot"],
        },
        headers=_auth(),
    )
    assert r.status_code == 400
    assert r.json()["error"] == "unsafe_webhook_endpoint"


def test_event_types_catalog() -> None:
    c = _client()
    r = c.get("/event-types", headers=_auth())
    assert r.status_code == 200
    assert "new_lot" in r.json()["data"]
    assert "deal_detected" in r.json()["data"]


def test_healthz_no_auth() -> None:
    c = _client()
    assert c.get("/healthz").json()["ok"] is True
