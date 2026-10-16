"""P8.1: provider-independent metrics port, bounded in-memory registry and
Prometheus text exposition (format 0.0.4).

Every metric is declared up front with its type and label names. Label values
must come from bounded sets (route templates, status codes, enum reasons);
identifiers of cases, runs or subjects are NEVER labels (privacy and
cardinality). Recording never raises into the caller: an instrumentation
fault must not change request outcomes.

An OpenTelemetry adapter can implement the same port (``opentelemetry-api``
is already a transitive dependency of FastAPI, but exporting requires the SDK
and an exporter, a deployment choice): proposed, see DECISIONS.md ADR-027.
"""
import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Lock

_log = logging.getLogger("medrixa.metrics")
DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    kind: str  # counter | histogram | gauge
    help: str
    labels: tuple[str, ...] = ()


DEFINITIONS: dict[str, MetricDefinition] = {d.name: d for d in (
    MetricDefinition("medrixa_http_requests_total", "counter", "HTTP requests by route template and status.",
                     ("method", "route", "status")),
    MetricDefinition("medrixa_http_request_duration_seconds", "histogram", "HTTP request latency.",
                     ("method", "route")),
    MetricDefinition("medrixa_rate_limited_total", "counter", "Requests refused by a rate limit.", ("scope",)),
    MetricDefinition("medrixa_auth_failures_total", "counter", "Failed authentications (uniform 401)."),
    MetricDefinition("medrixa_capacity_rejections_total", "counter", "Run creations refused for capacity."),
    MetricDefinition("medrixa_runs_total", "counter", "Run creation requests by outcome.", ("outcome",)),
    MetricDefinition("medrixa_run_stop_reasons_total", "counter", "Finished runs by stop reason.", ("reason",)),
    MetricDefinition("medrixa_lease_renewals_total", "counter", "Successful lease renewals."),
    MetricDefinition("medrixa_lease_lost_total", "counter", "Executions stopped because the lease was lost."),
    MetricDefinition("medrixa_recovery_runs_total", "counter", "Runs examined by recovery, by result.", ("result",)),
    MetricDefinition("medrixa_audit_failures_total", "counter", "Audit writes that failed (operation refused)."),
    MetricDefinition("medrixa_active_runs", "gauge", "Run executions in progress in this process."),
    MetricDefinition("medrixa_db_pool_connections", "gauge", "Database pool connections by state.", ("state",)),
)}


class Metrics(ABC):
    @abstractmethod
    def increment(self, name: str, labels: Mapping[str, str] | None = None, value: float = 1.0) -> None: ...

    @abstractmethod
    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None: ...

    @abstractmethod
    def set_gauge(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None: ...


class NullMetrics(Metrics):
    def increment(self, name, labels=None, value=1.0):
        pass

    def observe(self, name, value, labels=None):
        pass

    def set_gauge(self, name, value, labels=None):
        pass


def _key(definition: MetricDefinition, labels: Mapping[str, str] | None) -> tuple[str, ...]:
    labels = labels or {}
    if set(labels) != set(definition.labels):
        raise ValueError("label names do not match the metric definition")
    return tuple(str(labels[name]) for name in definition.labels)


class InMemoryMetrics(Metrics):
    """Thread-safe registry. ``max_series`` bounds memory if a label ever misbehaves."""

    def __init__(self, buckets: tuple[float, ...] = DEFAULT_BUCKETS, max_series: int = 5_000):
        self.buckets = tuple(sorted(buckets))
        self.max_series = max_series
        self._lock = Lock()
        self._values: dict[str, dict[tuple[str, ...], float]] = {}
        self._histograms: dict[str, dict[tuple[str, ...], tuple[list[int], float, int]]] = {}
        self._collectors: list[Callable[["InMemoryMetrics"], None]] = []
        self.dropped = 0

    def _series(self, store: dict, name: str) -> dict:  # noqa: D102
        return store.setdefault(name, {})

    def _guard(self, fn):
        try:
            fn()
        except Exception:
            # Instrumentation must never break a request; constant message, no values.
            _log.warning("metric not recorded")

    def _room(self, series: dict, key: tuple[str, ...]) -> bool:
        if key in series:
            return True
        total = sum(len(s) for s in self._values.values()) + sum(len(s) for s in self._histograms.values())
        if total >= self.max_series:
            self.dropped += 1
            return False
        return True

    def increment(self, name, labels=None, value=1.0):
        def record():
            definition = DEFINITIONS[name]
            if definition.kind != "counter" or value < 0 or not math.isfinite(value):
                raise ValueError("invalid counter update")
            key = _key(definition, labels)
            with self._lock:
                series = self._series(self._values, name)
                if self._room(series, key):
                    series[key] = series.get(key, 0.0) + value
        self._guard(record)

    def set_gauge(self, name, value, labels=None):
        def record():
            definition = DEFINITIONS[name]
            if definition.kind != "gauge" or not math.isfinite(value):
                raise ValueError("invalid gauge update")
            key = _key(definition, labels)
            with self._lock:
                series = self._series(self._values, name)
                if self._room(series, key):
                    series[key] = float(value)
        self._guard(record)

    def observe(self, name, value, labels=None):
        def record():
            definition = DEFINITIONS[name]
            if definition.kind != "histogram" or value < 0 or not math.isfinite(value):
                raise ValueError("invalid histogram observation")
            key = _key(definition, labels)
            with self._lock:
                series = self._series(self._histograms, name)
                if not self._room(series, key):
                    return
                counts, total, count = series.get(key, ([0] * len(self.buckets), 0.0, 0))
                counts = [c + (1 if value <= bound else 0) for c, bound in zip(counts, self.buckets, strict=True)]
                series[key] = (counts, total + value, count + 1)
        self._guard(record)

    def add_collector(self, collector: Callable[["InMemoryMetrics"], None]) -> None:
        """Callback run at scrape time (e.g. pool and capacity gauges)."""
        with self._lock:
            self._collectors.append(collector)

    def value(self, name: str, labels: Mapping[str, str] | None = None) -> float:
        """Current counter/gauge value (tests and diagnostics)."""
        definition = DEFINITIONS[name]
        with self._lock:
            return self._values.get(name, {}).get(_key(definition, labels), 0.0)

    def histogram_count(self, name: str, labels: Mapping[str, str] | None = None) -> int:
        definition = DEFINITIONS[name]
        with self._lock:
            return self._histograms.get(name, {}).get(_key(definition, labels), ([], 0.0, 0))[2]

    def render_prometheus(self) -> str:
        for collector in list(self._collectors):
            self._guard(lambda c=collector: c(self))
        lines: list[str] = []
        with self._lock:
            for name, definition in DEFINITIONS.items():
                lines.append(f"# HELP {name} {definition.help}")
                lines.append(f"# TYPE {name} {definition.kind}")
                if definition.kind == "histogram":
                    for key, (counts, total, count) in sorted(self._histograms.get(name, {}).items()):
                        base = _labels(definition.labels, key)
                        for bound, cumulative in zip(self.buckets, counts, strict=True):
                            lines.append(f"{name}_bucket{_labels(definition.labels, key, ('le', _num(bound)))} "
                                         f"{cumulative}")
                        lines.append(f"{name}_bucket{_labels(definition.labels, key, ('le', '+Inf'))} {count}")
                        lines.append(f"{name}_sum{base} {_num(total)}")
                        lines.append(f"{name}_count{base} {count}")
                else:
                    for key, number in sorted(self._values.get(name, {}).items()):
                        lines.append(f"{name}{_labels(definition.labels, key)} {_num(number)}")
        return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(names: tuple[str, ...], values: tuple[str, ...], extra: tuple[str, str] | None = None) -> str:
    pairs = list(zip(names, values, strict=True)) + ([extra] if extra else [])
    if not pairs:
        return ""
    return "{" + ",".join(f'{n}="{_escape(v)}"' for n, v in pairs) + "}"


def _num(value: float) -> str:
    return repr(float(value)) if not float(value).is_integer() else str(int(value))
