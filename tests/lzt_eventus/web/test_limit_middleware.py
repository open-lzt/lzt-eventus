"""`LimitValidationMiddleware` runs before routing — applies to any `?limit=`, generically."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from eventus_fakes import fake_engine_handle
from lzt_eventus.config import EngineConfig
from lzt_eventus.web.main import build_app

ADMIN = "admin-secret-key"


def _client(*, max_query_limit: int = 500) -> TestClient:
    cfg = EngineConfig(admin_api_key=SecretStr(ADMIN), max_query_limit=max_query_limit)
    return TestClient(build_app(fake_engine_handle(cfg)))


def _auth() -> dict[str, str]:
    return {"X-API-Key": ADMIN}


def test_subscriptions_list_rejects_limit_too_large() -> None:
    c = _client()
    r = c.get("/subscriptions/list", params={"limit": 999_999}, headers=_auth())
    assert r.status_code == 400
    assert r.json()["error"] == "limit_too_large"


def test_subscriptions_list_within_bound_passes_through() -> None:
    c = _client()
    r = c.get("/subscriptions/list", params={"limit": 10}, headers=_auth())
    assert r.status_code == 200


def test_max_query_limit_is_configurable() -> None:
    c = _client(max_query_limit=5)
    r = c.get("/subscriptions/list", params={"limit": 10}, headers=_auth())
    assert r.status_code == 400
    assert r.json()["error"] == "limit_too_large"
    assert r.json()["detail"]["max_limit"] == 5


def test_limit_error_carries_request_id() -> None:
    c = _client()
    r = c.get(
        "/subscriptions/list",
        params={"limit": 999_999},
        headers={**_auth(), "X-Request-ID": "test-req-123"},
    )
    assert r.json()["request_id"] == "test-req-123"
    assert r.headers["X-Request-ID"] == "test-req-123"


def test_no_limit_param_is_unaffected() -> None:
    c = _client()
    r = c.get("/subscriptions/list", headers=_auth())
    assert r.status_code == 200


def test_negative_limit_is_invalid_not_too_large() -> None:
    c = _client()
    r = c.get("/subscriptions/list", params={"limit": -1}, headers=_auth())
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_limit"
