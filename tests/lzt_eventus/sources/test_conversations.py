from __future__ import annotations

from datetime import UTC, datetime

from pylzt.lib.clock import FakeClock
from pylzt.methods.conversations import ListConversationMessages, ListConversations
from pylzt.models.conversation import Conversation, Message
from pylzt.pagination import Page

from eventus_fakes import FakeCursorStore, FakeEventLog, FakeLastSeenStore, FakeSeenCache
from lzt_eventus.events.message import NewConversation, NewMessage
from lzt_eventus.sources.conversations import ConversationsSource
from lzt_eventus.transport import LogTransport

_CONVERSATION = Conversation(
    conversation_id=1,
    conversation_title="Deal #1",
    creator_user_id=42,
    creator_username="seller",
    conversation_update_date=1_000,
    is_unread=True,
    folder="unread",
)

_MESSAGE_1 = Message(
    message_id=10,
    conversation_id=1,
    creator_user_id=42,
    message_body_plain_text="hello",
    message_create_date=900,
    message_is_system=False,
)
_MESSAGE_2 = Message(
    message_id=11,
    conversation_id=1,
    creator_user_id=1,
    message_body_plain_text="hi back",
    message_create_date=950,
    message_is_system=False,
)


class FakeBus:
    def __init__(self) -> None:
        self.notified = 0

    def notify(self) -> None:
        self.notified += 1


class FakeClient:
    """Stub `Client.execute` — one fixed conversation + a mutable message list."""

    def __init__(self, conversations: list[Conversation], messages: list[Message]) -> None:
        self._conversations = conversations
        self._messages = messages

    async def execute(self, method: object) -> Page[Conversation] | Page[Message]:
        if isinstance(method, ListConversations):
            return Page(items=self._conversations, has_more=False)
        if isinstance(method, ListConversationMessages):
            return Page(items=self._messages, has_more=False)
        raise AssertionError(f"unexpected method: {method!r}")


def _source(
    client: FakeClient, log: FakeEventLog, last_seen: FakeLastSeenStore, bus: FakeBus
) -> ConversationsSource:
    return ConversationsSource(
        client=client,  # type: ignore[arg-type]
        transport=LogTransport(log, on_committed=bus.notify),
        seen=FakeSeenCache(),
        cursor_store=FakeCursorStore(),
        last_seen=last_seen,
        min_cadence=1.0,
        max_cadence=60.0,
        cadence=5.0,
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
    )


async def test_new_conversation_and_new_messages_emitted_once() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()
    client = FakeClient([_CONVERSATION], [_MESSAGE_1, _MESSAGE_2])
    source = _source(client, log, last_seen, bus)

    emitted = await source.poll_once()

    assert emitted == 3  # NewConversation + 2 NewMessage
    assert bus.notified == 1
    new_conversations = [e for e in log._events if isinstance(e, NewConversation)]
    new_messages = [e for e in log._events if isinstance(e, NewMessage)]
    assert len(new_conversations) == 1
    assert new_conversations[0].conversation_id == 1
    assert {m.message_id for m in new_messages} == {10, 11}


async def test_second_poll_with_unchanged_fixture_emits_nothing_new() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()
    client = FakeClient([_CONVERSATION], [_MESSAGE_1, _MESSAGE_2])
    source = _source(client, log, last_seen, bus)

    first = await source.poll_once()
    second = await source.poll_once()

    assert first == 3
    assert second == 0
    assert len(log._events) == 3  # no duplicate append on replay


async def test_new_message_in_known_conversation_is_the_only_emission() -> None:
    last_seen = FakeLastSeenStore()
    log = FakeEventLog(last_seen)
    bus = FakeBus()
    client = FakeClient([_CONVERSATION], [_MESSAGE_1])
    source = _source(client, log, last_seen, bus)

    await source.poll_once()  # seeds conversation + message 10

    advanced_conversation = Conversation(
        conversation_id=_CONVERSATION.conversation_id,
        conversation_title=_CONVERSATION.conversation_title,
        creator_user_id=_CONVERSATION.creator_user_id,
        creator_username=_CONVERSATION.creator_username,
        conversation_update_date=_CONVERSATION.conversation_update_date + 1,
        is_unread=True,
        folder=_CONVERSATION.folder,
    )
    client._conversations = [advanced_conversation]
    client._messages = [_MESSAGE_1, _MESSAGE_2]

    emitted = await source.poll_once()

    assert emitted == 1
    new_messages = [e for e in log._events if isinstance(e, NewMessage)]
    assert {m.message_id for m in new_messages} == {10, 11}
