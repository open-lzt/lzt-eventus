"""Marker base for web services (type-identity + DI discovery, no behaviour)."""

from __future__ import annotations

from abc import ABC


class BaseService(ABC):
    """All web services inherit this; logic lives in the subclass."""
