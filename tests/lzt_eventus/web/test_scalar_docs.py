"""Self-hosted Scalar reference: gated by `web_docs_enabled`, points at our OpenAPI spec."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from eventus_fakes import fake_engine_handle
from lzt_eventus.config import EngineConfig
from lzt_eventus.web.main import build_app

ADMIN = "admin-secret-key"


def test_scalar_page_served_when_docs_enabled() -> None:
    cfg = EngineConfig(admin_api_key=SecretStr(ADMIN), web_docs_enabled=True)
    client = TestClient(build_app(fake_engine_handle(cfg)))
    r = client.get("/scalar")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "/openapi.json" in r.text
    assert "@scalar/api-reference" in r.text


def test_scalar_page_absent_when_docs_disabled() -> None:
    cfg = EngineConfig(admin_api_key=SecretStr(ADMIN), web_docs_enabled=False)
    client = TestClient(build_app(fake_engine_handle(cfg)))
    assert client.get("/scalar").status_code == 404


def test_openapi_spec_is_reachable() -> None:
    cfg = EngineConfig(admin_api_key=SecretStr(ADMIN), web_docs_enabled=True)
    client = TestClient(build_app(fake_engine_handle(cfg)))
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "lzt-core management API"
