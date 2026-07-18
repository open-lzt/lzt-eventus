"""`BaseSubscriptionRepo` — the storage contract `delivery/` depends on.

Concrete implementations (Memory/Postgres) live in `web.repos.subscription_repo`;
this module stays free of any storage-technology import so `delivery/` never
depends on `web/`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from lzt_eventus.delivery.subscription import Subscription, SubscriptionId


class BaseSubscriptionRepo(ABC):
    @abstractmethod
    async def get(self, subscription_id: SubscriptionId) -> Subscription | None: ...

    @abstractmethod
    async def list(
        self, *, limit: int, offset: int, active_only: bool = False
    ) -> Sequence[Subscription]: ...

    @abstractmethod
    async def count(self, *, active_only: bool = False) -> int: ...

    @abstractmethod
    async def add(self, sub: Subscription) -> Subscription: ...

    @abstractmethod
    async def replace(self, sub: Subscription) -> Subscription: ...
