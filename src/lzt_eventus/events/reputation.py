"""Reputation domain event — self-account like/dislike counter changes."""

from __future__ import annotations

from typing import ClassVar

from lzt_eventus.events.base import DomainEvent, EventType


class RatingChanged(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.RATING_CHANGED
    user_like_count: int
    user_dislike_count: int
    delta_likes: int
    delta_dislikes: int
