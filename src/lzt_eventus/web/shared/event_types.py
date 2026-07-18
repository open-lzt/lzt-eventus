"""Parse `EventType` names off the wire — one `unknown_event_type` error everywhere.

Shared by subscription registration/update and the `/events/pending` type filter so
an unrecognized name always fails the same way, not a bespoke 422 on some endpoints.
"""

from __future__ import annotations

from collections.abc import Sequence

from lzt_eventus.events.base import EventType
from lzt_eventus.web.base.errors import UnknownEventType


def parse_event_types(names: Sequence[str]) -> frozenset[EventType]:
    parsed: set[EventType] = set()
    for name in names:
        try:
            parsed.add(EventType(name))
        except ValueError as exc:
            raise UnknownEventType(event_type=name) from exc
    return frozenset(parsed)
