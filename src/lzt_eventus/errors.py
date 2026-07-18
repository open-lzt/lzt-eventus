"""Typed engine errors carrying args, never pre-formatted text."""

from __future__ import annotations


class EngineError(Exception):
    """Root of the event-engine error tree."""


class CursorConflict(EngineError):
    """Optimistic cursor commit lost a race (stale expected_version)."""

    def __init__(self, consumer: str, expected: int, actual: int) -> None:
        self.consumer = consumer
        self.expected = expected
        self.actual = actual
        super().__init__(f"cursor {consumer}: expected v{expected}, found v{actual}")


class AlreadyRunning(EngineError):
    """A second engine instance tried to start while the advisory lock is held."""

    def __init__(self, lock_key: int) -> None:
        self.lock_key = lock_key
        super().__init__(f"engine already running (advisory lock {lock_key})")


class BaselineMissing(EngineError):
    """A diff was attempted without a durable baseline (programmer error)."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


class SignatureInvalid(EngineError):
    """An inbound webhook failed signature verification — reject the delivery."""

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(source)


class DuplicateSource(EngineError):
    """A source was added under a name already registered with the engine."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"source already registered: {name}")


class SourceNotFound(EngineError):
    """A source was removed by a name the engine does not currently run."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"no such source: {name}")


class SourceExhausted(EngineError):
    """A source's task kept exiting unexpectedly past the configured restart cap."""

    def __init__(self, name: str, attempts: int) -> None:
        self.name = name
        self.attempts = attempts
        super().__init__(f"source {name} exhausted restart attempts: {attempts}")


class ConsumerNotFound(EngineError):
    """A subscriber was removed by a name the bus has not registered."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"no such consumer: {name}")


class EmptySourceUnits(EngineError):
    """A RotatingSource was constructed with zero poll units — nothing to rotate."""

    def __init__(self) -> None:
        super().__init__("RotatingSource requires at least one poll unit")


class InvalidAccountsPerTick(EngineError):
    """A RotatingSource was constructed with a non-positive accounts_per_tick."""

    def __init__(self, accounts_per_tick: int) -> None:
        self.accounts_per_tick = accounts_per_tick
        super().__init__(f"accounts_per_tick must be >= 1, got {accounts_per_tick}")
