"""Prometheus adapter for the `BaseMetrics` seam — daemon layer only (Law 27).

The libraries (`pylzt`, `lzt_eventus`) emit through `BaseMetrics` and never
import prometheus. The daemon binds this concrete impl; tests use `NullMetrics`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pylzt.lib.metrics import BaseMetrics

if TYPE_CHECKING:
    from prometheus_client import CollectorRegistry


class PrometheusMetrics(BaseMetrics):
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        from prometheus_client import CollectorRegistry

        self._registry = registry or CollectorRegistry()
        self._counters: dict[str, Any] = {}
        self._gauges: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}

    def _counter(self, name: str, labels: tuple[str, ...]) -> Any:
        from prometheus_client import Counter

        if name not in self._counters:
            self._counters[name] = Counter(f"lzt_{name}", name, labels, registry=self._registry)
        return self._counters[name]

    def _gauge(self, name: str, labels: tuple[str, ...]) -> Any:
        from prometheus_client import Gauge

        if name not in self._gauges:
            self._gauges[name] = Gauge(f"lzt_{name}", name, labels, registry=self._registry)
        return self._gauges[name]

    def _histogram(self, name: str, labels: tuple[str, ...]) -> Any:
        from prometheus_client import Histogram

        if name not in self._histograms:
            self._histograms[name] = Histogram(f"lzt_{name}", name, labels, registry=self._registry)
        return self._histograms[name]

    def incr(self, name: str, value: int = 1, **labels: str) -> None:
        metric = self._counter(name, tuple(labels))
        (metric.labels(**labels) if labels else metric).inc(value)

    def gauge(self, name: str, value: float, **labels: str) -> None:
        metric = self._gauge(name, tuple(labels))
        (metric.labels(**labels) if labels else metric).set(value)

    def observe(self, name: str, value: float, **labels: str) -> None:
        metric = self._histogram(name, tuple(labels))
        (metric.labels(**labels) if labels else metric).observe(value)

    def render(self) -> bytes:
        from prometheus_client import generate_latest

        return generate_latest(self._registry)
