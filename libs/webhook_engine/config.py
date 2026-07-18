"""Tunables for the webhook delivery lib — no coupling to any host engine's config."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WebhookEngineConfig:
    max_attempts: int = 5
    backoff_base: float = 0.5
    backoff_max: float = 30.0
    timeout: float = 10.0
    max_response_bytes: int = 65536
    # Ceiling for a receiver-requested `retry_after` (see sink.py) — defaults to
    # `backoff_max` so an out-of-box config can't be stalled longer than the
    # existing exponential-backoff ceiling already allows.
    retry_after_cap: float = 30.0
