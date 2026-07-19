"""`EngineConfig` — every operational knob, loaded from env (no secret in repo).

Tokens and store URLs come from the environment (`LZT_*`); cadences, budgets and
retention are tunable with safe defaults. Frozen after load so a running daemon
cannot mutate its own config.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from pylzt.types import Category


@dataclass(frozen=True, slots=True)
class WebhookConfig:
    """Grouped view over `EngineConfig`'s webhook-push delivery knobs."""

    max_attempts: int
    backoff_base: float
    backoff_max: float
    timeout: float
    idle_poll: float
    max_subscriptions: int


@dataclass(frozen=True, slots=True)
class CadenceConfig:
    """Grouped view over `EngineConfig`'s per-source polling cadences."""

    min_cadence: float
    max_cadence: float
    default_cadence: float
    payments_cadence: float
    notif_cadence: float
    conversations_cadence: float
    rating_cadence: float
    guarantee_check_interval: float
    account_reconcile_cadence: float


class EngineConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LZT_", env_file=".env", extra="ignore", frozen=True
    )

    tokens: list[SecretStr] = Field(default_factory=list)
    database_url: str = "postgresql://lzt:lzt@localhost:5432/pylzt"
    redis_url: str = "redis://localhost:6379/0"

    categories: list[Category] = Field(default_factory=lambda: [Category.STEAM])
    min_cadence: float = 6.0
    max_cadence: float = 120.0
    default_cadence: float = 30.0
    per_page: int = 50
    poll_pages: int = 1  # how many catalog pages to scan per poll (newest-first)

    disappear_polls: int = 3
    confirm_budget_fraction: float = 0.25  # share of the general bucket for confirms
    confirm_batch_size: int = 50

    # Cadences for the event-source sources (Market payments/Forum notif+conv+rating).
    payments_cadence: float = 30.0
    notif_cadence: float = 15.0
    conversations_cadence: float = 15.0
    guarantee_check_interval: float = 3600.0
    rating_cadence: float = 5.0  # per-account tick inside the rating RotatingSource
    rating_accounts_per_tick: int = 1  # how many rating accounts RotatingSource polls per tick

    seen_ttl_seconds: int = 86_400
    batch_size: int = 50
    batch_linger: float = 0.05

    max_handle_attempts: int = 5
    # Backoff between failed handle() attempts before DLQ park (base * 2**attempt, capped).
    catchup_backoff_base: float = 1.0
    catchup_backoff_max: float = 30.0
    # Bulkhead on the catch-up bus: max in-process consumers pumping at once.
    # 0 = unbounded (one worker per consumer dispatches freely).
    bus_max_concurrent_consumers: int = 0
    retention_months: int = 3
    max_sink_lag: int = 100_000

    # Webhook-push delivery (the sink owns retry+backoff; the delivery bus parks on exhaust).
    webhook_max_attempts: int = 5
    webhook_backoff_base: float = 0.5
    webhook_backoff_max: float = 30.0
    webhook_timeout: float = 10.0
    delivery_idle_poll: float = 0.25
    delivery_max_subscriptions: int = 1000

    warn_window_hours: float = 24.0
    deal_threshold: float = 0.85  # price < ai_price * threshold => DealDetected

    health_host: str = "127.0.0.1"
    health_port: int = 27543
    advisory_lock_key: int = 0x6C7A_7463  # "lztc"
    run_id: str = "engine"
    admin_api_key: SecretStr = SecretStr("")  # management-API admin key (LZT_ADMIN_API_KEY)
    lolz_webhook_secret: SecretStr = SecretStr("")  # HMAC key for inbound Lolz webhooks
    # Fernet key for token_account.token_ciphertext (LZT_TOKEN_ENC_KEY). Crown-jewel
    # secret — losing it makes every stored token unrecoverable. `secret_box.SecretBox`
    # fails loud (RuntimeError) at construction if this is empty; never falls back to
    # storing plaintext.
    token_enc_key: SecretStr = SecretStr("")
    # How often the token-account reconciler's periodic safety sweep runs, on top of
    # the immediate reconcile the admin service triggers after every mutation.
    account_reconcile_cadence: float = 60.0
    # Abuse-control cap on the number of *active* token accounts an admin-key holder
    # can register (the daemon actively polls every one of them).
    max_token_accounts: int = 100
    web_docs_enabled: bool = True
    # Upper bound for any `?limit=` query param, enforced by `LimitValidationMiddleware`
    # before routing — the single source of truth (routes no longer duplicate `le=`).
    max_query_limit: int = 500
    # Testnet override for pylzt's API base URL (LZT_API_BASE_URL). None keeps
    # pylzt's own production default — zero behavior change unless set.
    lzt_api_base_url: str | None = None

    @property
    def general_per_min(self) -> int:
        return 20

    @property
    def search_per_min(self) -> int:
        return 10

    @property
    def webhook(self) -> WebhookConfig:
        return WebhookConfig(
            max_attempts=self.webhook_max_attempts,
            backoff_base=self.webhook_backoff_base,
            backoff_max=self.webhook_backoff_max,
            timeout=self.webhook_timeout,
            idle_poll=self.delivery_idle_poll,
            max_subscriptions=self.delivery_max_subscriptions,
        )

    @property
    def cadence(self) -> CadenceConfig:
        return CadenceConfig(
            min_cadence=self.min_cadence,
            max_cadence=self.max_cadence,
            default_cadence=self.default_cadence,
            payments_cadence=self.payments_cadence,
            notif_cadence=self.notif_cadence,
            conversations_cadence=self.conversations_cadence,
            rating_cadence=self.rating_cadence,
            guarantee_check_interval=self.guarantee_check_interval,
            account_reconcile_cadence=self.account_reconcile_cadence,
        )
