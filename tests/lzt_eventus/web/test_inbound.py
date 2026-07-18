"""Inbound Lolz webhook: HMAC gate + idempotent append."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from pydantic import SecretStr

from eventus_fakes import fake_engine_handle
from lzt_eventus.config import EngineConfig
from lzt_eventus.events.base import EventType
from lzt_eventus.web.main import build_app
from lzt_eventus.web.shared.handle import EngineHandle

SECRET = "lolz-webhook-secret"


def _sign(raw: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _client() -> tuple[TestClient, EngineHandle]:
    cfg = EngineConfig(lolz_webhook_secret=SecretStr(SECRET))
    handle = fake_engine_handle(cfg)
    return TestClient(build_app(handle)), handle


def _max_seq(handle: EngineHandle) -> int:
    return asyncio.run(handle.event_log.max_seq())


def _body() -> bytes:
    return json.dumps(
        {"invoice_id": "inv-42", "status": "paid", "event_id": "evt-1", "amount": "10.00"}
    ).encode()


def test_forged_signature_rejected_nothing_appended() -> None:
    client, handle = _client()
    raw = _body()
    resp = client.post("/inbound/invoice", content=raw, headers={"X-Signature": "sha256=deadbeef"})
    assert resp.status_code == 401
    assert resp.json()["error"] == "signature_invalid"
    assert _max_seq(handle) == 0


def test_missing_signature_rejected() -> None:
    client, handle = _client()
    resp = client.post("/inbound/invoice", content=_body())
    assert resp.status_code == 401
    assert _max_seq(handle) == 0


def test_valid_signature_appends_and_dedups_on_replay() -> None:
    client, handle = _client()
    raw = _body()
    headers = {"X-Signature": _sign(raw)}

    first = client.post("/inbound/invoice", content=raw, headers=headers)
    assert first.status_code == 200
    assert first.json()["ok"] is True
    assert _max_seq(handle) == 1

    events = asyncio.run(handle.event_log.read_after(0, 10))
    assert events[0].event_type is EventType.INVOICE_PAID
    assert events[0].payload["invoice_id"] == "inv-42"

    replay = client.post("/inbound/invoice", content=raw, headers=headers)
    assert replay.status_code == 200
    assert _max_seq(handle) == 1  # idempotent — no second append
