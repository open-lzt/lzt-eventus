"""`RatingSource` — polls one account's Forum profile for like/dislike deltas.

Designed to be wrapped by a sibling `RotatingSource` (plan decision #10) for
multi-account rotation — this class itself only polls exactly ONE account's
profile per `poll_once()` call, single-account by design.

Reuses `BaseLastSeenStore` (built for category/item snapshots) to persist the
previous like/dislike counters between polls, per `~/.claude/rules/patterns.md`
Discover-before-write — same reuse `GuaranteeWatcher` (`sources/guarantee.py`)
already makes for its per-item watch-list. The store's `Category`/`ItemId`
keying doesn't map onto a single per-account counter pair, so this source
shares `GuaranteeWatcher`'s `Category.OTHER` bucket but reserves its own disjoint
key range (`_RATING_KEY_OFFSET`), keyed by `Profile.user_id` rather than
`item_id` so one shared store safely tracks several rotated accounts.

First observation of an account has no prior snapshot to diff against: the
baseline is established and nothing is emitted (deferred-first-signal — the
same convention `CategorySource._bootstrap` uses for `SnapshotInitialized`,
just without a marker event since there is nothing downstream needs to react to
on a rating cold-start).

`account_alias` (optional) tags the emitted event's `payload["account_alias"]`
exactly like `_category_payload` tags `payload["category"]`
(`diff/differ.py`) — a subscription's `filters={"account_alias": "..."}` then
matches with zero change to the payload-agnostic matcher
(`consumers/consumer.py::BaseSubscription.matches`). Set by `AccountReconciler` for
a per-account source it built from a `TokenAccount`'s primary alias; the
engine-level `rating_clients` wiring (`engine.py::_build_rating_source`) leaves
it `None` — those events are not account-scoped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from pylzt.client import Client
from pylzt.methods.users import GetSelfProfile
from pylzt.types import Category, ItemId

from lzt_eventus.baseline.store import BaseLastSeenStore, LastSeenBatch
from lzt_eventus.diff.snapshot import BaselineEntry
from lzt_eventus.events.base import AggregateId
from lzt_eventus.events.reputation import RatingChanged
from lzt_eventus.lib.clock import Clock, RealClock
from lzt_eventus.sources.base import BaseSource
from lzt_eventus.transport import BaseTransport

# Disjoint from GuaranteeWatcher's `_ITEM_KEY_OFFSET` (10**15 + item_id) so the
# two consumers sharing `Category.OTHER` never collide on the same stored row.
_RATING_KEY_OFFSET: Final[int] = 2 * 10**15
_BUCKET: Final[Category] = Category.OTHER


@dataclass(frozen=True, slots=True)
class _RatingSnapshot:
    likes: int
    dislikes: int


def _store_key(user_id: int) -> ItemId:
    return ItemId(user_id + _RATING_KEY_OFFSET)


def _encode(likes: int, dislikes: int) -> str:
    return json.dumps({"likes": likes, "dislikes": dislikes})


def _decode(raw: str) -> _RatingSnapshot | None:
    try:
        data = json.loads(raw)
        return _RatingSnapshot(likes=int(data["likes"]), dislikes=int(data["dislikes"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None  # foreign row sharing the bucket (or corrupt) — not ours to touch


class RatingSource(BaseSource):
    """Polls one account's Forum profile and emits `RatingChanged` on a delta."""

    def __init__(
        self,
        *,
        client: Client,
        transport: BaseTransport,
        last_seen: BaseLastSeenStore,
        min_cadence: float,
        max_cadence: float,
        cadence: float,
        clock: Clock | None = None,
        account_alias: str | None = None,
    ) -> None:
        super().__init__(
            min_cadence=min_cadence,
            max_cadence=max_cadence,
            cadence=cadence,
            clock=clock or RealClock(),
        )
        self.name = "source:rating"
        self._client = client
        self._transport = transport
        self._last_seen = last_seen
        self._clock = clock or RealClock()
        self._account_alias = account_alias

    async def poll_once(self) -> int:
        profile = await self._client.execute(GetSelfProfile())
        key = _store_key(profile.user_id)

        baseline = await self._last_seen.get_baseline(_BUCKET)
        prior_entry = baseline.get(key)
        prior = _decode(prior_entry.content_hash) if prior_entry is not None else None

        epoch = await self._last_seen.get_poll_epoch(_BUCKET) + 1
        upserts = {
            key: BaselineEntry(
                price=Decimal(0),
                content_hash=_encode(profile.user_like_count, profile.user_dislike_count),
            )
        }
        batch = LastSeenBatch(category=_BUCKET, poll_epoch=epoch, upserts=upserts)

        if prior is None:
            await self._transport.send([], batch)
            return 0

        delta_likes = profile.user_like_count - prior.likes
        delta_dislikes = profile.user_dislike_count - prior.dislikes
        if delta_likes == 0 and delta_dislikes == 0:
            await self._transport.send([], batch)
            return 0

        payload: dict[str, object] = (
            {"account_alias": self._account_alias} if self._account_alias else {}
        )
        event = RatingChanged.build(
            aggregate_id=AggregateId(str(profile.user_id)),
            occurred_at=self._clock.now(),
            content_hash=f"rating:{profile.user_id}:{profile.user_like_count}:{profile.user_dislike_count}",
            poll_epoch=epoch,
            payload=payload,
            user_like_count=profile.user_like_count,
            user_dislike_count=profile.user_dislike_count,
            delta_likes=delta_likes,
            delta_dislikes=delta_dislikes,
        )
        await self._transport.send([event], batch)
        return 1
