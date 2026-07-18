"""`EngineConfig` grouped-view properties (`.webhook` / `.cadence`)."""

from __future__ import annotations

from lzt_eventus.config import EngineConfig


def test_webhook_property_matches_flat_fields() -> None:
    config = EngineConfig()
    webhook = config.webhook

    assert webhook.max_attempts == config.webhook_max_attempts
    assert webhook.backoff_base == config.webhook_backoff_base
    assert webhook.backoff_max == config.webhook_backoff_max
    assert webhook.timeout == config.webhook_timeout
    assert webhook.idle_poll == config.delivery_idle_poll
    assert webhook.max_subscriptions == config.delivery_max_subscriptions


def test_cadence_property_matches_flat_fields() -> None:
    config = EngineConfig()
    cadence = config.cadence

    assert cadence.min_cadence == config.min_cadence
    assert cadence.max_cadence == config.max_cadence
    assert cadence.default_cadence == config.default_cadence
    assert cadence.payments_cadence == config.payments_cadence
    assert cadence.notif_cadence == config.notif_cadence
    assert cadence.conversations_cadence == config.conversations_cadence
    assert cadence.rating_cadence == config.rating_cadence
    assert cadence.guarantee_check_interval == config.guarantee_check_interval
    assert cadence.account_reconcile_cadence == config.account_reconcile_cadence
