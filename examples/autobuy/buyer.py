"""Buyer — a single balance-diff-verified purchase against the live lzt.market API.

The watch/filter half now lives in `lzt_eventus` (the local devkit server subscribes
and filters by category + event type); this example keeps only the buy side, whose one
gotcha is load-bearing: a `ValidationError` from `purchasing_fast_buy` does NOT mean
the purchase failed, so success is confirmed by a balance drop, never by the response.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError
from pylzt.client import Client
from pylzt.errors import LztError
from pylzt.types import ItemId


@dataclass(frozen=True, slots=True)
class PurchaseResult:
    item_id: int
    price: Decimal
    ok: bool
    error: str | None = None


class Buyer:
    """One balance-diff-verified purchase against lzt.market."""

    def __init__(self, client: Client) -> None:
        self._client = client

    async def buy(self, item_id: ItemId, price: Decimal) -> PurchaseResult:
        # `purchasing_fast_buy`'s response model has known codegen drift against the
        # live API (fields declared required the API omits, int fields that come back
        # float) — a `ValidationError` here does NOT mean the purchase failed; the POST
        # already landed. Balance-diff is the only way to tell truth from a client-side
        # parse bug without trusting the response.
        balance_before = await self._balance()
        try:
            await self._client.market.purchasing_fast_buy(item_id=int(item_id), price=float(price))
            ok, error = True, None
        except LztError as exc:
            ok, error = False, str(exc)
        except ValidationError as exc:
            ok = await self._balance() < balance_before
            error = None if ok else f"ambiguous response parse error: {exc}"
        return PurchaseResult(item_id=int(item_id), price=price, ok=ok, error=error)

    async def _balance(self) -> Decimal:
        profile = await self._client.market.profile_get()
        return Decimal(str(profile.balance))
