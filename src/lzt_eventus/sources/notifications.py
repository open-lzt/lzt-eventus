"""`NotificationsSource` — polls both Forum notification feeds (`market`/`nomarket`).

One source, two independent cheap-check/cursor pairs (`notif:market`/`notif:nomarket`,
`00-overview.md` decisions #6/#8) rather than two source classes, since both feeds
share the same cadence and cheap-check-then-fetch shape. `ListNotifications`/
`Notification` don't expose the upstream `notifications_total` counter to the
caller (only `Page.has_more`), so the cheap-check compares the *newest*
`notification_id` from a `limit=1` fetch against the per-kind cursor watermark
instead of a total-count diff — same "skip the full page fetch when nothing
changed" effect, just keyed off the field the DTO actually surfaces.

`notification_id` monotonicity is unverified upstream (`api-events-sources.md:38`),
so replay-safety comes from `BaseSeenCache` content-hash dedup (id + type +
created_at), never from the cursor — the cursor is only a cheap-check watermark.
Seen-marks and the cursor commit happen only *after* `log.append` succeeds, so a
crash between fetch and append re-processes the same page next cycle instead of
silently losing it.

`content_type` → concrete-event dispatch lives in the domain
(`events/notification.py:parse_notification`), next to the event definitions, so
this source stays a thin poll loop. Unmatched types fall back to
`MarketNotificationReceived`/`ForumNotificationReceived` depending on which feed
produced the notification.

Shares `BaseLastSeenStore`'s `Category.OTHER` bucket with `GuaranteeWatcher` purely
for its append-time `poll_epoch` counter (upserts/drops stay empty here — no real
baseline data is stored). `MemoryLastSeenStore.apply()` unconditionally overwrites
a category's stored epoch, so two independent *resetters* would clobber each
other; this source instead reads-then-increments the same way `GuaranteeWatcher`
does, so the shared counter only ever grows regardless of poll order.
"""

from __future__ import annotations

from typing import Final

from pylzt.client import Client
from pylzt.methods.notifications import ListNotifications
from pylzt.types import Category, ItemId

from lzt_eventus.baseline.store import BaseLastSeenStore, LastSeenBatch
from lzt_eventus.cursor.base import BaseCursorStore
from lzt_eventus.dedup.seen import BaseSeenCache
from lzt_eventus.events.base import DomainEvent
from lzt_eventus.events.notification import (
    ForumNotificationReceived,
    MarketNotificationReceived,
    parse_notification,
)
from lzt_eventus.lib.clock import Clock, RealClock
from lzt_eventus.sources.base import BaseSource
from lzt_eventus.transport import BaseTransport

_CHEAP_CHECK_LIMIT: Final[int] = 1
_PAGE_LIMIT: Final[int] = 50
_EPOCH_BUCKET: Final[Category] = Category.OTHER

_KINDS: Final[tuple[tuple[str, str, type[DomainEvent]], ...]] = (
    ("market", "notif:market", MarketNotificationReceived),
    ("nomarket", "notif:nomarket", ForumNotificationReceived),
)


class NotificationsSource(BaseSource):
    def __init__(
        self,
        *,
        client: Client,
        transport: BaseTransport,
        last_seen: BaseLastSeenStore,
        cursors: BaseCursorStore,
        seen: BaseSeenCache,
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
        self.name = "source:notifications"
        self._client = client
        self._transport = transport
        self._last_seen = last_seen
        self._cursors = cursors
        self._seen = seen

    async def poll_once(self) -> int:
        epoch = await self._last_seen.get_poll_epoch(_EPOCH_BUCKET) + 1
        events: list[DomainEvent] = []
        marks: list[tuple[ItemId, str]] = []
        for kind, consumer, fallback_cls in _KINDS:
            kind_events, kind_marks = await self._poll_kind(kind, consumer, fallback_cls, epoch)
            events.extend(kind_events)
            marks.extend(kind_marks)
        if not events:
            return 0

        await self._transport.send(events, LastSeenBatch(category=_EPOCH_BUCKET, poll_epoch=epoch))
        for item_id, content_hash in marks:
            await self._seen.mark(item_id, content_hash)
        return len(events)

    async def _poll_kind(
        self,
        kind: str,
        consumer: str,
        fallback_cls: type[DomainEvent],
        epoch: int,
    ) -> tuple[list[DomainEvent], list[tuple[ItemId, str]]]:
        cheap = await self._client.execute(ListNotifications(type=kind, limit=_CHEAP_CHECK_LIMIT))
        if not cheap.items:
            return [], []

        state = await self._cursors.get(consumer)
        newest_id = cheap.items[0].notification_id
        if newest_id <= state.last_seq:
            return [], []  # cheap-check: newest id unchanged since the last full fetch

        page = await self._client.execute(ListNotifications(type=kind, limit=_PAGE_LIMIT))
        events: list[DomainEvent] = []
        marks: list[tuple[ItemId, str]] = []
        max_seen_id = state.last_seq
        for notification in page.items:
            max_seen_id = max(max_seen_id, notification.notification_id)
            # ItemId is a bare int NewType; reused here as an opaque dedup key,
            # not a lot item id (BaseSeenCache's key type is structurally int-only).
            item_id = ItemId(notification.notification_id)
            content_hash = f"{notification.content_type}:{notification.created_at}"
            if await self._seen.is_seen(item_id, content_hash):
                continue
            events.append(
                parse_notification(
                    notification,
                    kind=kind,
                    fallback_cls=fallback_cls,
                    content_hash=content_hash,
                    poll_epoch=epoch,
                )
            )
            marks.append((item_id, content_hash))

        if max_seen_id != state.last_seq:
            await self._cursors.commit(consumer, max_seen_id, state.version)
        return events, marks
