"""Event-egress routes — SSE + WebSocket, per-subscription stream-token gated.

The token is read from a header (`Authorization: Bearer` or `X-Stream-Token`),
never from the query string — query strings leak into access logs. Resume is
zero-gap: SSE honours the `Last-Event-ID` request header, WS honours `last_seq` in
the auth frame, and both feed the cursor straight into `StreamService` so a
reconnect re-reads from the last scanned seq, skipping nothing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from lzt_eventus.delivery.subscription import Subscription, SubscriptionId
from lzt_eventus.web.base.errors import Unauthorized
from lzt_eventus.web.schemas.dtos import WsAuth
from lzt_eventus.web.services.streams import StreamFrame, StreamService
from lzt_eventus.web.shared.deps import HandleDep
from lzt_eventus.web.shared.handle import EngineHandle
from lzt_eventus.web.shared.security import extract_bearer, verify_stream_token

router = APIRouter(prefix="/streams", tags=["streams"])

_WS_QUEUE_MAX = 256
_WS_DRAIN_INTERVAL = 0.5


async def _resolve_sub(
    handle: EngineHandle, subscription_id: str, token: str | None
) -> Subscription:
    sub = await handle.subscriptions.get(SubscriptionId(subscription_id))
    if sub is None or sub.stream_token_hash is None:
        raise Unauthorized(reason="invalid stream token")
    verify_stream_token(token, sub.stream_token_hash)
    return sub


def _sse_frame(frame: StreamFrame) -> str:
    return (
        f"id: {frame.seq}\n"
        f"event: {frame.event_type.value}\n"
        f"data: {json.dumps(frame.data, ensure_ascii=False)}\n\n"
    )


def _parse_last_event_id(raw: str | None) -> int:
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


@router.get("/sse")
async def sse(
    handle: HandleDep,
    request: Request,
    subscription_id: Annotated[str, Query()],
    authorization: Annotated[str | None, Header()] = None,
    x_stream_token: Annotated[str | None, Header(alias="X-Stream-Token")] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    token = extract_bearer(authorization, x_stream_token)
    sub = await _resolve_sub(handle, subscription_id, token)
    svc = StreamService(handle.event_log)
    after = _parse_last_event_id(last_event_id)

    async def _body() -> AsyncIterator[str]:
        cursor = after
        while True:
            batch = await svc.catch_up(sub, cursor)
            for frame in batch.frames:
                yield _sse_frame(frame)
            cursor = batch.next_seq
            if batch.drained:
                break
        stop = asyncio.Event()
        try:
            async for frame in svc.live(sub, cursor, stop):
                yield _sse_frame(frame)
        finally:
            stop.set()

    return StreamingResponse(_body(), media_type="text/event-stream")


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    handle: EngineHandle = websocket.app.state.handle
    await websocket.accept()
    try:
        raw = await websocket.receive_json()
        auth = WsAuth.model_validate(raw)
    except (ValidationError, KeyError, ValueError):
        await websocket.close(code=1008)
        return

    try:
        sub = await _resolve_sub(handle, auth.subscription_id, auth.token)
    except Unauthorized:
        await websocket.close(code=1008)
        return

    await _pump_ws(websocket, StreamService(handle.event_log), sub, auth.last_seq)


async def _pump_ws(
    websocket: WebSocket, svc: StreamService, sub: Subscription, last_seq: int
) -> None:
    queue: asyncio.Queue[StreamFrame] = asyncio.Queue(maxsize=_WS_QUEUE_MAX)
    stop = asyncio.Event()

    async def _produce() -> None:
        cursor = last_seq
        while True:
            batch = await svc.catch_up(sub, cursor)
            for frame in batch.frames:
                await queue.put(frame)
            cursor = batch.next_seq
            if batch.drained:
                break
        async for frame in svc.live(sub, cursor, stop):
            await queue.put(frame)

    async def _watch_disconnect() -> None:
        try:
            while True:
                await websocket.receive()
        except WebSocketDisconnect:
            stop.set()

    producer = asyncio.create_task(_produce())
    watcher = asyncio.create_task(_watch_disconnect())
    try:
        while not stop.is_set():
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=_WS_DRAIN_INTERVAL)
            except TimeoutError:
                continue
            await websocket.send_json(
                {"seq": frame.seq, "event_type": frame.event_type.value, "data": frame.data}
            )
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        producer.cancel()
        watcher.cancel()
