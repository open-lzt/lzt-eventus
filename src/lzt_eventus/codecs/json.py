"""The single JSON codec (no registry — D14/Law 0).

Turns a `DomainEvent` (including a concrete subclass's extra fields like `lot`)
into a JSON-safe payload dict for the `event_log.payload` column and the delivery
wire, and rebuilds a base `DomainEvent` on read. Evolution is additive-only
(`extra` ignored on read); a breaking change later adds a `BaseEventUpcaster`.

A Postgres round-trip yields a *base* `DomainEvent` carrying the full data in
`payload` — which is exactly what the webhook/SSE/WS wire needs. In-process
(Memory log) consumers keep the concrete subclass type for richer typing.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import Enum
from functools import cache
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from lzt_eventus.events.base import AggregateId, DomainEvent, EventType

# DomainEvent is now a pydantic BaseModel — model_fields (not dataclasses.fields)
# is the field-name source; ClassVar EVENT_TYPE is excluded from both the same way.
_BASE_FIELDS = set(DomainEvent.model_fields)


@cache
def _extra_field_names(event_cls: type[DomainEvent]) -> tuple[str, ...]:
    """Subclass-specific field names (everything past the base envelope), cached per class."""
    return tuple(name for name in event_cls.model_fields if name not in _BASE_FIELDS)


def to_jsonable(obj: Any) -> Any:
    """Recursively coerce a value into JSON-safe primitives (no float money)."""
    if obj is None or isinstance(obj, str | bool | int):
        return obj
    if isinstance(obj, Decimal):
        return str(obj)  # money as string — never float
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, frozenset | set | tuple | list):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, BaseModel):
        # python-mode dump keeps Decimal/datetime as-is, then to_jsonable applies the
        # codec's own rules (money → string, never float) rather than pydantic's.
        return to_jsonable(obj.model_dump())
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    return str(obj)


def encode_event(event: DomainEvent) -> dict[str, Any]:
    """Flatten an event's payload + subclass-specific fields into one JSON dict."""
    out: dict[str, Any] = dict(event.payload)
    for name in _extra_field_names(type(event)):
        out[name] = to_jsonable(getattr(event, name))
    return out


def event_envelope(event: DomainEvent) -> dict[str, Any]:
    """The canonical wire shape shared by SSE/WS frames and the webhook body.

    One serialization for every egress (Law 3): the flattened payload plus the
    base envelope fields, all JSON-safe.
    """
    data: dict[str, Any] = dict(encode_event(event))
    data.update(
        event_id=str(event.event_id),
        event_type=event.event_type.value,
        aggregate_id=str(event.aggregate_id),
        occurred_at=event.occurred_at.isoformat(),
        seq=event.seq,
        schema_version=event.schema_version,
    )
    return data


def canonical_bytes(data: Mapping[str, Any]) -> bytes:
    """Deterministic JSON bytes — the exact payload that gets HMAC-signed and sent.

    `sort_keys` makes the signature reproducible by the receiver, who re-signs the
    raw body it got off the wire.
    """
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode()


def decode_event(
    *,
    event_id: UUID,
    event_type: str,
    aggregate_id: str,
    occurred_at: datetime,
    content_hash: str,
    schema_version: int,
    seq: int,
    payload: Mapping[str, Any],
) -> DomainEvent:
    """Rebuild a base `DomainEvent` from a persisted row (concrete type collapses)."""
    return DomainEvent(
        event_id=event_id,
        aggregate_id=AggregateId(aggregate_id),
        occurred_at=occurred_at,
        content_hash=content_hash,
        schema_version=schema_version,
        seq=seq,
        payload=dict(payload),
        _event_type=EventType(event_type),
    )
