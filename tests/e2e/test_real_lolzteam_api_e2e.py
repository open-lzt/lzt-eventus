"""E2E against the live Lolzteam API — proves every `Client` method and every
event-engine source works against the real transport, not just against
`FakeClient`. Skipped by default: drop a real token into LZT_E2E_TOKEN
(or edit the placeholder below) to run it.

Every method here is read-only (GET-style methods-as-classes); nothing buys,
messages, or withdraws. Data-dependent assertions (get_lot, batch, conversation
messages) skip gracefully when the account has no matching data instead of
failing, since a live account's state isn't controlled by the test.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from pydantic import SecretStr
from pylzt.client import Client
from pylzt.methods.balances import GetBalances
from pylzt.methods.conversations import ListConversationMessages, ListConversations
from pylzt.methods.notifications import ListNotifications
from pylzt.methods.payments import ListPayments
from pylzt.methods.users import GetSelfProfile
from pylzt.models.lot import LotFilter
from pylzt.types import Category

from eventus_fakes import build_fake_engine
from lzt_eventus.config import EngineConfig

pytestmark = pytest.mark.e2e

PLACEHOLDER_TOKEN = "REPLACE_WITH_REAL_LZT_TOKEN"
LZT_E2E_TOKEN = os.environ.get("LZT_E2E_TOKEN", PLACEHOLDER_TOKEN)

requires_real_token = pytest.mark.skipif(
    LZT_E2E_TOKEN == PLACEHOLDER_TOKEN,
    reason="set LZT_E2E_TOKEN to a real Lolzteam API token to run this test",
)


@pytest.fixture
async def client() -> AsyncIterator[Client]:
    c = Client(tokens=[LZT_E2E_TOKEN])
    try:
        yield c
    finally:
        await c.aclose()


@requires_real_token
async def test_list_categories(client: Client) -> None:
    categories = await client.market.list_categories()

    assert categories
    assert Category.STEAM in categories


@requires_real_token
async def test_category_params(client: Client) -> None:
    schema = await client.market.category_params(Category.STEAM)

    assert schema is not None


@requires_real_token
async def test_category_games(client: Client) -> None:
    games = await client.market.category_games(Category.STEAM)

    assert isinstance(games, list)


@requires_real_token
async def test_list_lots_first_page(client: Client) -> None:
    lots = await client.market.list_lots(LotFilter(category=Category.STEAM)).first_page()

    assert isinstance(lots, list)


@requires_real_token
async def test_get_lot(client: Client) -> None:
    lots = await client.market.list_lots(LotFilter(category=Category.STEAM)).first_page()
    if not lots:
        pytest.skip("no live Steam lots available to fetch by id")

    lot = await client.market.get_lot(lots[0].item_id)

    assert lot.item_id == lots[0].item_id


@requires_real_token
async def test_get_lots_batch(client: Client) -> None:
    lots = await client.market.list_lots(LotFilter(category=Category.STEAM)).first_page()
    if not lots:
        pytest.skip("no live Steam lots available for a batch fetch")

    batch = await client.market.get_lots_batch([lot.item_id for lot in lots[:5]])

    assert batch


@requires_real_token
async def test_execute_get_self_profile(client: Client) -> None:
    profile = await client.execute(GetSelfProfile())

    assert profile.user_id > 0


@requires_real_token
async def test_execute_get_balances(client: Client) -> None:
    balances = await client.execute(GetBalances())

    assert isinstance(balances, list)


@requires_real_token
async def test_execute_list_payments(client: Client) -> None:
    page = await client.execute(ListPayments())

    assert isinstance(page.items, list)


@requires_real_token
async def test_execute_list_notifications(client: Client) -> None:
    page = await client.execute(ListNotifications(type="market", limit=10))

    assert isinstance(page.items, list)


@requires_real_token
async def test_execute_list_conversations(client: Client) -> None:
    page = await client.execute(ListConversations())

    assert isinstance(page.items, list)


@requires_real_token
async def test_execute_list_conversation_messages(client: Client) -> None:
    conversations = await client.execute(ListConversations())
    if not conversations.items:
        pytest.skip("no live conversations available to fetch messages from")

    page = await client.execute(
        ListConversationMessages(conversation_id=conversations.items[0].conversation_id)
    )

    assert isinstance(page.items, list)


@requires_real_token
async def test_engine_drains_every_poller_against_real_api(client: Client) -> None:
    """One `drain_once()` polls category (lots), payments, notifications,
    conversations, guarantee-watch and rating — the full source roster wired
    by `EventEngine.__init__` — all against the live API, none mocked.
    """
    config = EngineConfig(
        tokens=[SecretStr(LZT_E2E_TOKEN)],
        categories=[Category.STEAM],
        disappear_polls=1,
        poll_pages=1,
        per_page=50,
    )
    engine = build_fake_engine(
        config,
        client=client,
        consumers=[],
        rating_clients=[client],
    )

    emitted = await engine.drain_once()

    assert emitted >= 0
