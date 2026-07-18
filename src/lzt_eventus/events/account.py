"""Account / guarantee events (3-D).

Modeled in wave-02 so subscribers type against them from day one; *produced* in
wave-03 once the authenticated sources (guarantee scheduler, claims source) land.
This consumer supersedes the planned `future.py` (consolidated — no dead interim).
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from lzt_eventus.events.base import DomainEvent, EventType


class GuaranteeExpiring(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.GUARANTEE_EXPIRING
    item_id: int
    guarantee_end: datetime


class AccountWentInvalid(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.ACCOUNT_INVALID
    item_id: int
    reason: str = ""


class DisputeOpened(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.DISPUTE_OPENED
    claim_id: int
    item_id: int | None = None


class ClaimFiled(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.CLAIM_FILED
    claim_id: int
    item_id: int | None = None
