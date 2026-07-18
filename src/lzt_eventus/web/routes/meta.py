"""Meta + health routes — event-type discovery, liveness, readiness, metrics.

(Consolidated here from the wave-02 aiohttp health server — one served app.)
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from lzt_eventus.events.base import EventType
from lzt_eventus.web.base.errors import ServiceUnavailable
from lzt_eventus.web.schemas.envelopes import DataResponse, Success
from lzt_eventus.web.shared.deps import AdminDep, HandleDep

router = APIRouter(tags=["meta"])


@router.get("/event-types")
async def event_types(_: AdminDep) -> DataResponse[list[str]]:
    """The subscribable event catalog (for client discovery)."""
    return DataResponse(data=sorted(e.value for e in EventType))


@router.get("/healthz")
async def healthz() -> Success:
    return Success()


@router.get("/readyz")
async def readyz(handle: HandleDep) -> Success:
    if not await handle.ready():
        raise ServiceUnavailable(reason="dependencies not ready")
    return Success()


@router.get("/metrics")
async def metrics(handle: HandleDep) -> Response:
    body = handle.render_metrics() if handle.render_metrics is not None else b""
    return Response(content=body, media_type="text/plain; version=0.0.4")
