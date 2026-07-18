"""Token-account management API over TestClient: admin-key gate, register, redaction."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from eventus_fakes import fake_engine_handle
from lzt_eventus.config import EngineConfig
from lzt_eventus.web.main import build_app

ADMIN = "admin-secret-key"


def _client() -> TestClient:
    cfg = EngineConfig(admin_api_key=SecretStr(ADMIN), token_enc_key=SecretStr("api-test-key"))
    return TestClient(build_app(fake_engine_handle(cfg)))


def _auth() -> dict[str, str]:
    return {"X-API-Key": ADMIN}


def test_token_routes_require_admin_key() -> None:
    c = _client()
    assert c.get("/tokens/list").status_code == 401


def test_register_get_by_alias_and_deactivate_round_trip() -> None:
    c = _client()
    with patch(
        "lzt_eventus.web.services.token_accounts.Client.execute", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.return_value = object()
        r = c.post(
            "/tokens/register",
            json={"token": "raw-lzt-token", "alias": "my-alias"},
            headers=_auth(),
        )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["token"] is None  # redacted on register response

    got = c.get("/tokens/by-alias", params={"alias": "my-alias"}, headers=_auth())
    assert got.status_code == 200
    assert got.json()["data"]["token"] == "raw-lzt-token"  # revealed only here

    listed = c.get("/tokens/list", headers=_auth()).json()["items"]
    assert listed[0]["token"] is None  # redacted on list

    deact = c.post("/tokens/deactivate", json={"account_id": data["account_id"]}, headers=_auth())
    assert deact.json()["ok"] is True
    active = c.get("/tokens/list", params={"active_only": True}, headers=_auth())
    assert active.json()["total"] == 0


def test_by_alias_unknown_returns_404() -> None:
    c = _client()
    r = c.get("/tokens/by-alias", params={"alias": "nope"}, headers=_auth())
    assert r.status_code == 404
    assert r.json()["error"] == "alias_not_found"
