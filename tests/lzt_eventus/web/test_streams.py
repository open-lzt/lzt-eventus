"""SSE + WebSocket egress over TestClient: auth, catch-up, zero-gap resume.

SSE is exercised by driving the ASGI app directly and cancelling once enough
frames arrive: the sync `TestClient`/`httpx` deadlock on an endless stream that
flows through Starlette's `BaseHTTPMiddleware` (a known limitation), whereas a
direct ASGI drive consumes the exact frames the app emits.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pylzt.types import Category
from starlette.websockets import WebSocketDisconnect

from eventus_fakes import fake_engine_handle
from lzt_eventus.baseline.store import LastSeenBatch
from lzt_eventus.config import EngineConfig
from lzt_eventus.delivery.subscription import Subscription, SubscriptionId, TransportKind
from lzt_eventus.delivery.subscription_ctx import SseCtx
from lzt_eventus.events.base import AggregateId, DomainEvent, EventType, make_event_id
from lzt_eventus.web.main import build_app
from lzt_eventus.web.shared.handle import EngineHandle
from lzt_eventus.web.shared.security import hash_stream_token

ADMIN = "admin-secret-key"
TOKEN = "stream-token-xyz"


def _event(event_type: EventType, agg: str, payload: dict[str, object]) -> DomainEvent:
    return DomainEvent(
        event_id=make_event_id(AggregateId(agg), event_type, agg, 0),
        aggregate_id=AggregateId(agg),
        occurred_at=datetime.now(UTC),
        content_hash=agg,
        payload=payload,
        _event_type=event_type,
    )


def _make_handle() -> EngineHandle:
    cfg = EngineConfig(admin_api_key=SecretStr(ADMIN))
    handle = fake_engine_handle(cfg)
    sub = Subscription(
        subscription_id=SubscriptionId("sub-1"),
        transport=TransportKind.SSE,
        endpoint="client-1",
        event_types=frozenset({EventType.NEW_LOT}),
        created_at=datetime.now(UTC),
        ctx=SseCtx(),
        stream_token_hash=hash_stream_token(TOKEN),
    )

    async def _seed() -> None:
        await handle.subscriptions.add(sub)
        # seq 1 + 3 match (new_lot); seq 2 is a non-matching event in between.
        await handle.event_log.append(
            [
                _event(EventType.NEW_LOT, "a1", {"title": "first"}),
                _event(EventType.PRICE_DROPPED, "a2", {"title": "skip"}),
                _event(EventType.NEW_LOT, "a3", {"title": "second"}),
            ],
            LastSeenBatch(category=Category.OTHER, poll_epoch=0),
        )

    asyncio.run(_seed())
    return handle


async def _drive_sse(
    app: FastAPI, *, token: str | None, count: int, last_event_id: str | None = None
) -> tuple[int, str]:
    headers = [(b"host", b"t")]
    if token is not None:
        headers.append((b"x-stream-token", token.encode()))
    if last_event_id is not None:
        headers.append((b"last-event-id", last_event_id.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/streams/sse",
        "raw_path": b"/streams/sse",
        "query_string": b"subscription_id=sub-1",
        "headers": headers,
        "scheme": "http",
        "server": ("t", 80),
        "client": ("c", 1),
    }
    chunks: list[str] = []
    status: dict[str, int] = {}
    done = asyncio.Event()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            raw_status = message["status"]
            assert isinstance(raw_status, int)
            status["code"] = raw_status
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if isinstance(body, bytes) and body:
                chunks.append(body.decode())
            if "".join(chunks).count("\n\n") >= count:
                done.set()

    task = asyncio.create_task(app(scope, receive, send))  # type: ignore[arg-type]
    try:
        await asyncio.wait_for(done.wait(), timeout=10)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
    return status["code"], "".join(chunks)


def _parse_sse(text: str, count: int) -> list[dict[str, str]]:
    frames: list[dict[str, str]] = []
    for block in text.split("\n\n"):
        stripped = block.strip("\n")
        if not stripped:
            continue
        frame: dict[str, str] = {}
        for line in stripped.split("\n"):
            key, _, val = line.partition(":")
            frame[key] = val.lstrip()
        frames.append(frame)
        if len(frames) >= count:
            break
    return frames


def test_sse_catch_up_delivers_matching_only() -> None:
    app = build_app(_make_handle())
    code, text = asyncio.run(_drive_sse(app, token=TOKEN, count=2))
    assert code == 200
    frames = _parse_sse(text, 2)
    assert [f["id"] for f in frames] == ["1", "3"]  # new_lot only; seq 2 skipped
    assert all(f["event"] == "new_lot" for f in frames)


def test_sse_resume_from_last_event_id_zero_gap() -> None:
    app = build_app(_make_handle())
    code, text = asyncio.run(_drive_sse(app, token=TOKEN, count=1, last_event_id="1"))
    assert code == 200
    frames = _parse_sse(text, 1)
    assert [f["id"] for f in frames] == ["3"]  # resumes after seq 1, no gap


def test_sse_rejects_absent_or_wrong_token() -> None:
    client = TestClient(build_app(_make_handle()))
    assert client.get("/streams/sse", params={"subscription_id": "sub-1"}).status_code == 401
    bad = client.get(
        "/streams/sse",
        params={"subscription_id": "sub-1"},
        headers={"X-Stream-Token": "nope"},
    )
    assert bad.status_code == 401


def test_ws_catch_up_then_resume() -> None:
    client = TestClient(build_app(_make_handle()))
    with client.websocket_connect("/streams/ws") as ws:
        ws.send_json({"subscription_id": "sub-1", "token": TOKEN, "last_seq": 0})
        first = ws.receive_json()
        second = ws.receive_json()
    assert [first["seq"], second["seq"]] == [1, 3]
    assert first["event_type"] == "new_lot"


def test_ws_bad_token_closes_1008() -> None:
    client = TestClient(build_app(_make_handle()))
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/streams/ws") as ws:
            ws.send_json({"subscription_id": "sub-1", "token": "wrong"})
            ws.receive_json()
    assert exc.value.code == 1008
