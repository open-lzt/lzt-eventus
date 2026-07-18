"""Devkit — one-call local runtime for embedding, examples and tests.

`local_eventus(...)` stands up a live in-memory engine + management API behind an
`async with`, the Law-30 quickstart sibling of `EventEngine.build_memory()`.
"""

from __future__ import annotations

from lzt_eventus.devkit.local import LocalEventus, local_eventus

__all__ = ["LocalEventus", "local_eventus"]
