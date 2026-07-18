"""Forum message-vertical domain events — new conversation + new message."""

from __future__ import annotations

from typing import ClassVar

from lzt_eventus.events.base import DomainEvent, EventType


class NewConversation(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.NEW_CONVERSATION
    conversation_id: int
    conversation_title: str
    creator_user_id: int
    creator_username: str
    folder: str


class NewMessage(DomainEvent):
    EVENT_TYPE: ClassVar[EventType] = EventType.NEW_MESSAGE
    message_id: int
    conversation_id: int
    author_user_id: int
    message_text: str
    is_system: bool
    folder: str
