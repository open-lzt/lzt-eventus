"""Inbound Lolz webhook ingest — verify HMAC, normalize, append to the log.

The raw body is HMAC-SHA256'd with `config.lolz_webhook_secret` and compared
constant-time against the `X-Signature: sha256=<hex>` header. A normalized invoice
becomes a base `DomainEvent` with a deterministic `event_id`, so a replay collides
on the log's UNIQUE constraint (idempotent at the source of truth); an in-process
seen-set short-circuits the common replay before it ever touches the log.

The exact Lolz payload + signature scheme is UNVERIFIED — the HMAC check and the
field mapping are implemented defensively and must be reconciled against a real
captured webhook before production use.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from pylzt.types import Category

from lzt_eventus.baseline.store import LastSeenBatch
from lzt_eventus.events.base import AggregateId, DomainEvent, EventType, make_event_id
from lzt_eventus.web.base.errors import BadRequest, WebhookSignatureInvalid
from lzt_eventus.web.schemas.envelopes import Success
from lzt_eventus.web.shared.deps import HandleDep

router = APIRouter(prefix="/inbound", tags=["inbound"])

# UNVERIFIED — assumed Lolz invoice states; reconcile against a real webhook.
_STATE_TO_EVENT: dict[str, EventType] = {
    "paid": EventType.INVOICE_PAID,
    "created": EventType.INVOICE_CREATED,
    "expired": EventType.INVOICE_EXPIRED,
}


class InboundInvoice(BaseModel):
    """Trust-boundary model for the raw Lolz invoice webhook body (UNVERIFIED)."""

    model_config = ConfigDict(extra="ignore")

    invoice_id: str
    status: str
    event_id: str | None = None
    amount: str | None = None
    currency: str | None = None

    def dedup_key(self) -> str:
        """Upstream idempotency key — explicit `event_id`, else invoice+status."""
        return self.event_id or f"{self.invoice_id}:{self.status}"


def _verify_signature(raw: bytes, header: str | None, secret: str) -> None:
    if not secret:
        raise WebhookSignatureInvalid(reason="webhook secret not configured")
    if not header or not header.startswith("sha256="):
        raise WebhookSignatureInvalid(reason="missing signature")
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(header[len("sha256=") :], expected):
        raise WebhookSignatureInvalid(reason="signature mismatch")


def _normalize(body: InboundInvoice, occurred_at: datetime) -> DomainEvent:
    event_type = _STATE_TO_EVENT.get(body.status, EventType.INVOICE_CREATED)
    aggregate_id = AggregateId(body.invoice_id)
    dedup = body.dedup_key()
    payload: dict[str, object] = {
        "invoice_id": body.invoice_id,
        "status": body.status,
        "amount": body.amount,
        "currency": body.currency,
    }
    return DomainEvent(
        event_id=make_event_id(aggregate_id, event_type, dedup, 0),
        aggregate_id=aggregate_id,
        occurred_at=occurred_at,
        content_hash=dedup,
        payload=payload,
        _event_type=event_type,
    )


@router.post("/invoice")
async def invoice(request: Request, handle: HandleDep) -> Success:
    raw = await request.body()
    secret = handle.config.lolz_webhook_secret.get_secret_value()
    _verify_signature(raw, request.headers.get("X-Signature"), secret)
    try:
        body = InboundInvoice.model_validate_json(raw)
    except ValidationError as exc:
        raise BadRequest(reason="malformed invoice body") from exc

    key = body.dedup_key()
    if key in handle.inbound_seen:
        return Success()

    event = _normalize(body, datetime.now(UTC))
    await handle.event_log.append([event], LastSeenBatch(category=Category.OTHER, poll_epoch=0))
    handle.inbound_seen.add(key)
    return Success()
