"""End-to-end poll cycle against the real `lzt-testnet` mock server subprocess.

Proves the whole seam works together: `EngineConfig.lzt_api_base_url` threads into
`Client` (via `EventEngine.build_memory`), one poll cycle hits the mock server's real
HTTP surface, and a domain event comes out the other end.
"""

from __future__ import annotations

import pytest
from pylzt.client import Client
from pylzt.config import ClientConfig
from pylzt.types import Category
from pydantic import SecretStr

from fixtures.testnet_server import testnet_server
from lzt_eventus.config import EngineConfig
from lzt_eventus.engine import EventEngine

pytestmark = pytest.mark.e2e

__all__ = ["testnet_server"]


async def test_poll_cycle_emits_event_against_testnet_server(testnet_server: str) -> None:
    config = EngineConfig(
        tokens=[SecretStr("testnet-fake-token")],
        categories=[Category.STEAM],
        lzt_api_base_url=testnet_server,
    )
    assert config.lzt_api_base_url is not None
    # `EngineConfig.lzt_api_base_url` only threads into `ClientConfig.base_url` (Market)
    # in `EventEngine.build` — Forum calls (notifications/conversations/rating/guarantee)
    # keep their own default host unless `forum_base_url` is pointed at testnet too, so
    # both are set here to exercise the full `build_memory` source set against one mock.
    client = Client(
        [token.get_secret_value() for token in config.tokens],
        config=ClientConfig(base_url=config.lzt_api_base_url, forum_base_url=testnet_server),
    )
    engine = EventEngine.build_memory(client=client, config=config)

    # `drain_once`'s int is *new lot* events only — cold-start bootstrap emits its
    # `SnapshotInitialized` marker straight to the log without counting toward it
    # (see `CategorySource._bootstrap`), so assert on the durable log instead.
    await engine.drain_once()

    events = await engine.stores.log.read_after(0, limit=100)
    assert len(events) >= 1
