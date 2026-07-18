"""`ConversationsSource` — turns Forum inbox conversations into message-vertical events.

Fetches the `unread` folder each tick (the endpoint itself is the first cheap
filter — only conversations with unread activity come back at all). A
conversation's presence is tracked via `BaseSeenCache` keyed off an offset
`ItemId` (mirrors `GuaranteeWatcher`'s reserved-keyspace trick so conversation
ids can never collide with a real lzt.market item id in the same cache): the
first sighting emits `NewConversation`; every sighting also checks a second
seen-mark keyed by `conversation_update_date` — unchanged + already-read means
nothing moved since the last tick, so the (expensive) per-conversation message
page is skipped. New/updated conversations fall through to
`ListConversationMessages`, and only messages newer than the per-conversation
cursor (`conv:{conversation_id}`, `last_seq` = highest `message_id` emitted)
become `NewMessage` events — the classic "cheap list, expensive detail on
change only" shape used by every other source in this package.

`BaseEventLog.append` requires a `LastSeenBatch` even though this source has no
catalog baseline of its own — it shares `Category.OTHER`'s epoch counter with
`GuaranteeWatcher`/`PaymentsSource` (Discover-before-write: no new storage ABC),
always via read-current-then-+1 so the shared counter only ever advances, never
regresses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

import structlog
from pylzt.client import Client
from pylzt.methods.conversations import ListConversationMessages, ListConversations
from pylzt.models.conversation import Conversation
from pylzt.types import Category, ItemId

from lzt_eventus.baseline.store import BaseLastSeenStore, LastSeenBatch
from lzt_eventus.cursor.base import BaseCursorStore
from lzt_eventus.dedup.seen import BaseSeenCache
from lzt_eventus.errors import CursorConflict
from lzt_eventus.events.base import AggregateId, DomainEvent
from lzt_eventus.events.message import NewConversation, NewMessage
from lzt_eventus.lib.clock import Clock, RealClock
from lzt_eventus.sources.base import BaseSource
from lzt_eventus.transport import BaseTransport

_log = structlog.get_logger("lzt_eventus.sources.conversations")

# Shared with `GuaranteeWatcher`/`PaymentsSource` — all three only ever read-then-+1
# this bucket's epoch, so cohabiting is safe (monotonic regardless of interleaving)
# despite none of them having a real catalog baseline to store here.
_BUCKET: Final[Category] = Category.OTHER

# Keeps conversation-id `BaseSeenCache` keys clear of any real lzt.market ItemId,
# same reserved-keyspace technique as `GuaranteeWatcher._ITEM_KEY_OFFSET`.
_CONVERSATION_KEY_OFFSET: Final[int] = 3 * 10**15

_EXISTS_MARK = "exists"
_INBOX_FOLDER = "unread"


def _conversation_key(conversation_id: int) -> ItemId:
    return ItemId(conversation_id + _CONVERSATION_KEY_OFFSET)


def _update_mark(update_date: int) -> str:
    return f"touched-at:{update_date}"


def _messages_consumer(conversation_id: int) -> str:
    return f"conv:{conversation_id}"


class ConversationsSource(BaseSource):
    def __init__(
        self,
        *,
        client: Client,
        transport: BaseTransport,
        seen: BaseSeenCache,
        cursor_store: BaseCursorStore,
        last_seen: BaseLastSeenStore,
        min_cadence: float,
        max_cadence: float,
        cadence: float,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(
            min_cadence=min_cadence,
            max_cadence=max_cadence,
            cadence=cadence,
            clock=clock or RealClock(),
        )
        self.name = "source:conversations"
        self._client = client
        self._transport = transport
        self._seen = seen
        self._cursor_store = cursor_store
        self._last_seen = last_seen
        self._clock = clock or RealClock()

    async def _new_messages(
        self, conversation: Conversation, epoch: int, now: datetime
    ) -> list[DomainEvent]:
        consumer = _messages_consumer(conversation.conversation_id)
        cursor_state = await self._cursor_store.get(consumer)
        page = await self._client.execute(
            ListConversationMessages(conversation_id=conversation.conversation_id)
        )
        new_messages = sorted(
            (m for m in page.items if m.message_id > cursor_state.last_seq),
            key=lambda m: m.message_id,
        )
        if not new_messages:
            return []

        events: list[DomainEvent] = [
            NewMessage.build(
                aggregate_id=AggregateId(str(message.message_id)),
                occurred_at=now,
                content_hash=f"message:{message.message_id}",
                poll_epoch=epoch,
                message_id=message.message_id,
                conversation_id=message.conversation_id,
                author_user_id=message.creator_user_id,
                message_text=message.message_body_plain_text,
                is_system=message.message_is_system,
                folder=conversation.folder,
            )
            for message in new_messages
        ]
        try:
            await self._cursor_store.commit(
                consumer, new_messages[-1].message_id, cursor_state.version
            )
        except CursorConflict:
            # Another writer already advanced this conversation's cursor this tick —
            # drop our events rather than double-emit; next poll re-derives them
            # from the (unmoved) cursor if they're still genuinely unseen.
            _log.warning("conversations_cursor_conflict", consumer=consumer)
            return []
        return events

    async def poll_once(self) -> int:
        conversations = await self._client.execute(ListConversations(folder=_INBOX_FOLDER))
        now = self._clock.now()
        epoch = await self._last_seen.get_poll_epoch(_BUCKET) + 1
        events: list[DomainEvent] = []

        for conversation in conversations.items:
            key = _conversation_key(conversation.conversation_id)
            is_new = not await self._seen.is_seen(key, _EXISTS_MARK)
            if is_new:
                events.append(
                    NewConversation.build(
                        aggregate_id=AggregateId(str(conversation.conversation_id)),
                        occurred_at=now,
                        content_hash=f"conversation:{conversation.conversation_id}",
                        poll_epoch=epoch,
                        conversation_id=conversation.conversation_id,
                        conversation_title=conversation.conversation_title,
                        creator_user_id=conversation.creator_user_id,
                        creator_username=conversation.creator_username,
                        folder=conversation.folder,
                    )
                )
                await self._seen.mark(key, _EXISTS_MARK)

            update_mark = _update_mark(conversation.conversation_update_date)

            unchanged = not is_new and await self._seen.is_seen(key, update_mark)
            if unchanged and not conversation.is_unread:
                continue  # cheap-check: nothing moved since we last looked

            events.extend(await self._new_messages(conversation, epoch, now))
            await self._seen.mark(key, update_mark)

        if events:
            await self._transport.send(events, LastSeenBatch(category=_BUCKET, poll_epoch=epoch))
        return len(events)
