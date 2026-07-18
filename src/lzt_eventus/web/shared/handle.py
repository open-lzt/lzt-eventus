"""`EngineHandle` — the running daemon's surface the web layer is given.

One engine (Law 2): the web app opens no stores of its own; it receives the
already-wired `subscriptions` repo, durable `event_log`, and `cursors` plus a
readiness probe. Tests build an in-process handle via `eventus_fakes.fake_engine_handle`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from lzt_eventus.account.reconciler import AccountReconciler
from lzt_eventus.account.repo import BaseTokenAccountRepo
from lzt_eventus.config import EngineConfig
from lzt_eventus.cursor.base import BaseCursorStore
from lzt_eventus.delivery.repo import BaseSubscriptionRepo
from lzt_eventus.log.base import BaseEventLog
from secret_box import SecretBox


@dataclass(frozen=True, slots=True)
class EngineHandle:
    config: EngineConfig
    subscriptions: BaseSubscriptionRepo
    event_log: BaseEventLog
    cursors: BaseCursorStore
    ready: Callable[[], Awaitable[bool]]
    token_accounts: BaseTokenAccountRepo
    secret_box: SecretBox
    account_reconciler: AccountReconciler
    render_metrics: Callable[[], bytes] | None = None
    # In-process dedup set for inbound webhooks (idempotent ingest, single process).
    inbound_seen: set[str] = field(default_factory=set)
