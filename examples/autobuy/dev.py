"""Autobuy demo — subscribe to a category's new lots via a local `lzt_eventus`
server and buy each one under budget, until a purchase cap is hit.

Everything except the ~10-line core in `autobuy_from_new_lots` is boilerplate:
CLI args, reading the token, building the market `Client`, and standing up the
local engine + management API with `lzt_eventus.devkit.local_eventus`.

Requires the management SDK (a SEPARATE package, not vendored here):

    pip install lzt-eventus-sdk

Token is read from the `LZT_TOKEN` env var only — never pass it on the command
line (shell history / `ps` exposure) and never hardcode it.

Usage:
  LZT_TOKEN=... python dev.py --category telegram --budget 50 --max-purchases 3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from decimal import Decimal

from buyer import Buyer, PurchaseResult
from lzt_eventus_sdk import (
    CategoryScope,
    EventType,
    ManagementClient,
    MarketCategory,
    SubscriptionTransport,
)
from pylzt.client import Client
from pylzt.types import Category, ItemId

from lzt_eventus.config import EngineConfig
from lzt_eventus.devkit import LocalEventus, local_eventus


async def autobuy_from_new_lots(
    server: LocalEventus,
    buyer: Buyer,
    category: MarketCategory,
    *,
    budget: Decimal,
    limit: int,
    cadence: float,
) -> list[PurchaseResult]:
    """The core: subscribe to a category's new lots and buy each one under budget."""
    results: list[PurchaseResult] = []
    async with ManagementClient(server.base_url, api_key=server.api_key) as mgmt:
        sub = await mgmt.create_subscription(
            transport=SubscriptionTransport.POLLING,
            endpoint="autobuy-demo",
            event_types=[EventType.NEW_LOT],
            scope=CategoryScope(category=category),
        )
        while len(results) < limit:
            batch = await mgmt.poll_pending(sub.subscription_id, event_type=[EventType.NEW_LOT], limit=100)
            for event in batch.items:
                lot = event.data["lot"]
                if Decimal(str(lot["price"])) <= budget:
                    results.append(await buyer.buy(ItemId(lot["item_id"]), Decimal(str(lot["price"]))))
            if batch.items:
                await mgmt.confirm_read(sub.subscription_id, up_to_seq=batch.next_seq)
            else:
                await asyncio.sleep(cadence)
    return results


def _token_from_env() -> str:
    token = os.environ.get("LZT_TOKEN")
    if not token:
        print("LZT_TOKEN env var is required (never pass the token as a CLI arg).", file=sys.stderr)
        raise SystemExit(1)
    return token


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--category", default="telegram")
    p.add_argument("--budget", type=Decimal, default=Decimal(50), help="Max price per lot, RUB")
    p.add_argument("--max-purchases", type=int, default=3)
    p.add_argument("--cadence", type=float, default=10.0, help="seconds between empty polls")
    return p.parse_args()


def _report(purchases: list[PurchaseResult]) -> None:
    ok = [p for p in purchases if p.ok]
    failed = [p for p in purchases if not p.ok]
    print(f"\n{len(ok)} purchased, {len(failed)} failed")
    for p in ok:
        print(f"  OK   item_id={p.item_id} price={p.price}")
    for p in failed:
        print(f"  FAIL item_id={p.item_id} price={p.price} error={p.error}")


async def main() -> None:
    args = _parse_args()
    token = _token_from_env()
    config = EngineConfig(categories=[Category(args.category)])
    client = Client(tokens=[token])
    try:
        async with local_eventus(client=client, config=config) as server:
            purchases = await autobuy_from_new_lots(
                server,
                Buyer(client),
                MarketCategory(args.category),
                budget=args.budget,
                limit=args.max_purchases,
                cadence=args.cadence,
            )
        _report(purchases)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
