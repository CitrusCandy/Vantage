"""Production Structured Metrics, Low-Cardinality Collectors & Prometheus Exporter.

Provides bounded, thread-safe metric primitives for:
- Counters with low-cardinality label dimensions
- Gauges for point-in-time state indicators
- Histograms with rolling sliding windows and exact percentile computation (P50, P90, P95, P99)
- Prometheus Exposition Format generation
- Centralized MetricsRegistry
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

# Bounded limit on tracked label combinations to prevent memory exhaustion
MAX_LABEL_COMBINATIONS = 200

# Default latency buckets (in milliseconds) for histograms
DEFAULT_LATENCY_BUCKETS_MS: Tuple[float, ...] = (
    5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0, 30000.0, 60000.0
)


def _format_labels_key(labels: Optional[Dict[str, str]]) -> str:
    """Format label dictionary into a canonical string key."""
    if not labels:
        return ""
    return ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))


# ============================================================================
# 1. Counter Metric
# ============================================================================

class CounterMetric:
    """Thread-safe monotonically increasing integer counter with bounded labels."""

    def __init__(self, name: str, description: str, unit: str = "count"):
        self.name = name
        self.description = description
        self.unit = unit
        self._counts: Dict[str, int] = {}
        self._labels_map: Dict[str, Dict[str, str]] = {}
        self._lock = threading.RLock()

    def inc(self, value: int = 1, labels: Optional[Dict[str, str]] = None) -> None:
        """Increment counter by value (must be >= 0)."""
        if value < 0:
            raise ValueError("Counter increments must be non-negative")
        key = _format_labels_key(labels)
        with self._lock:
            if key not in self._counts:
                if len(self._counts) >= MAX_LABEL_COMBINATIONS:
                    key = ""
                else:
                    self._counts[key] = 0
                    self._labels_map[key] = dict(labels or {})
            if key not in self._counts:
                self._counts[key] = 0
                self._labels_map[key] = dict(labels or {})
            self._counts[key] += value

    def get_value(self, labels: Optional[Dict[str, str]] = None) -> int:
        key = _format_labels_key(labels)
        with self._lock:
            return self._counts.get(key, 0)

    def get_total(self) -> int:
        with self._lock:
            return sum(self._counts.values())

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            samples = []
            for key, count in self._counts.items():
                samples.append({
                    "labels": self._labels_map.get(key, {}),
                    "value": count,
                })
            return {
                "name": self.name,
                "type": "counter",
                "description": self.description,
                "unit": self.unit,
                "total": sum(self._counts.values()),
                "samples": samples,
            }

    def reset(self) -> None:
        with self._lock:
            self._counts = {}
            self._labels_map = {}


# ============================================================================
# 2. Gauge Metric
# ============================================================================

class GaugeMetric:
    """Thread-safe point-in-time value gauge with bounded labels."""

    def __init__(self, name: str, description: str, unit: str = ""):
        self.name = name
        self.description = description
        self.unit = unit
        self._values: Dict[str, float] = {}
        self._labels_map: Dict[str, Dict[str, str]] = {}
        self._last_updated: Dict[str, float] = {}
        self._lock = threading.RLock()

    def set(self, value: Union[int, float], labels: Optional[Dict[str, str]] = None) -> None:
        """Set the gauge to a given numerical value."""
        key = _format_labels_key(labels)
        with self._lock:
            if key not in self._values:
                if len(self._values) >= MAX_LABEL_COMBINATIONS:
                    key = ""
                else:
                    self._labels_map[key] = dict(labels or {})
            if key not in self._labels_map:
                self._labels_map[key] = dict(labels or {})
            self._values[key] = float(value)
            self._last_updated[key] = time.time()

    def inc(self, delta: Union[int, float] = 1.0, labels: Optional[Dict[str, str]] = None) -> None:
        key = _format_labels_key(labels)
        with self._lock:
            cur = self._values.get(key, 0.0)
            if key not in self._labels_map:
                self._labels_map[key] = dict(labels or {})
            self._values[key] = cur + float(delta)
            self._last_updated[key] = time.time()

    def dec(self, delta: Union[int, float] = 1.0, labels: Optional[Dict[str, str]] = None) -> None:
        self.inc(-delta, labels)

    def get_value(self, labels: Optional[Dict[str, str]] = None) -> float:
        key = _format_labels_key(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            samples = []
            for key, val in self._values.items():
                samples.append({
                    "labels": self._labels_map.get(key, {}),
                    "value": round(val, 4),
                    "last_updated": self._last_updated.get(key, time.time()),
                })
            return {
                "name": self.name,
                "type": "gauge",
                "description": self.description,
                "unit": self.unit,
                "samples": samples,
            }

    def reset(self) -> None:
        with self._lock:
            self._values = {}
            self._labels_map = {}
            self._last_updated = {}



# ============================================================================
# 3. Histogram Metric
# ============================================================================

class HistogramMetric:
    """Thread-safe sliding-window histogram metric with percentiles and bucket distributions."""

    def __init__(
        self,
        name: str,
        description: str,
        unit: str = "ms",
        buckets: Sequence[float] = DEFAULT_LATENCY_BUCKETS_MS,
        max_samples: int = 1000,
        window_seconds: float = 3600.0,
    ):
        self.name = name
        self.description = description
        self.unit = unit
        self.buckets = sorted(buckets)
        self.max_samples = max(50, max_samples)
        self.window_seconds = window_seconds

        # Map key -> (deque of (timestamp, value), bucket_counts, total_count, total_sum)
        self._samples: Dict[str, deque] = {}
        self._bucket_counts: Dict[str, Dict[float, int]] = {}
        self._total_counts: Dict[str, int] = {}
        self._total_sums: Dict[str, float] = {}
        self._labels_map: Dict[str, Dict[str, str]] = {}
        self._lock = threading.RLock()

    def observe(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Observe a measured value (e.g. latency in milliseconds)."""
        key = _format_labels_key(labels)
        now = time.time()
        with self._lock:
            if key not in self._samples:
                if len(self._samples) >= MAX_LABEL_COMBINATIONS:
                    key = ""
                else:
                    self._samples[key] = deque(maxlen=self.max_samples)
                    self._bucket_counts[key] = {b: 0 for b in self.buckets}
                    self._total_counts[key] = 0
                    self._total_sums[key] = 0.0
                    self._labels_map[key] = dict(labels or {})

            if key not in self._samples:
                self._samples[key] = deque(maxlen=self.max_samples)
                self._bucket_counts[key] = {b: 0 for b in self.buckets}
                self._total_counts[key] = 0
                self._total_sums[key] = 0.0
                self._labels_map[key] = dict(labels or {})

            # Record sample
            self._samples[key].append((now, float(value)))
            self._total_counts[key] += 1
            self._total_sums[key] += float(value)

            for b in self.buckets:
                if value <= b:
                    self._bucket_counts[key][b] += 1

    def _prune_expired_samples(self, key: str, now: float) -> List[float]:
        """Prune samples outside the rolling window and return active values."""
        cutoff = now - self.window_seconds
        d = self._samples.get(key, deque())
        while d and d[0][0] < cutoff:
            d.popleft()
        return [val for _, val in d]

    def get_percentiles(self, labels: Optional[Dict[str, str]] = None) -> Dict[str, float]:
        """Calculate P50, P90, P95, P99, min, max, mean for active samples."""
        key = _format_labels_key(labels)
        now = time.time()
        with self._lock:
            values = self._prune_expired_samples(key, now)
            if not values:
                return {
                    "count": 0,
                    "window_samples": 0,
                    "sum": 0.0,
                    "mean": 0.0,
                    "min": 0.0,
                    "max": 0.0,
                    "p50": 0.0,
                    "p90": 0.0,
                    "p95": 0.0,
                    "p99": 0.0,
                }

            sorted_v = sorted(values)
            n = len(sorted_v)

            def _pct(p: float) -> float:
                idx = min(n - 1, max(0, int(math.ceil((p / 100.0) * n)) - 1))
                return round(sorted_v[idx], 2)

            return {
                "count": self._total_counts.get(key, len(values)),
                "window_samples": len(values),
                "sum": round(self._total_sums.get(key, sum(values)), 2),
                "mean": round(sum(values) / len(values), 2),
                "min": round(sorted_v[0], 2),
                "max": round(sorted_v[-1], 2),
                "p50": _pct(50.0),
                "p90": _pct(90.0),
                "p95": _pct(95.0),
                "p99": _pct(99.0),
            }

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            samples_res = []
            for key in list(self._samples.keys()):
                pct = self.get_percentiles(self._labels_map.get(key))
                buckets_data = {
                    str(b): self._bucket_counts[key].get(b, 0)
                    for b in self.buckets
                }
                samples_res.append({
                    "labels": self._labels_map.get(key, {}),
                    "percentiles": pct,
                    "buckets": buckets_data,
                })
            return {
                "name": self.name,
                "type": "histogram",
                "description": self.description,
                "unit": self.unit,
                "samples": samples_res,
            }

    def reset(self) -> None:
        with self._lock:
            self._samples = {}
            self._bucket_counts = {}
            self._total_counts = {}
            self._total_sums = {}
            self._labels_map = {}



# ============================================================================
# 4. Centralized Metrics Registry
# ============================================================================

class MetricsRegistry:
    """Centralized singleton registry maintaining all platform operational metrics."""

    def __init__(self):
        self._metrics: Dict[str, Union[CounterMetric, GaugeMetric, HistogramMetric]] = {}
        self._lock = threading.RLock()
        self._init_standard_metrics()

    def _init_standard_metrics(self) -> None:
        # HTTP & API Metrics
        self.register(CounterMetric("http_requests_total", "Total HTTP requests handled by endpoint and status"))
        self.register(HistogramMetric("http_request_duration_ms", "HTTP request processing latency in milliseconds"))

        # External Source Ingestion Metrics
        self.register(CounterMetric("source_fetch_total", "Total external source fetches by source and result"))
        self.register(HistogramMetric("source_fetch_duration_ms", "External source network request latency in ms"))
        self.register(GaugeMetric("source_freshness_seconds", "Seconds elapsed since last successful scrape for source", unit="seconds"))

        # End-to-End Pipeline & Clustering Metrics
        self.register(CounterMetric("pipeline_executions_total", "Total pipeline runs by status"))
        self.register(HistogramMetric("pipeline_duration_ms", "End-to-end pipeline execution time in ms"))
        self.register(HistogramMetric("pipeline_stage_duration_ms", "Execution time per pipeline stage in ms"))

        # Database & Governance Metrics
        self.register(HistogramMetric("database_query_duration_ms", "Database query execution latency in ms"))
        self.register(GaugeMetric("database_connections_active", "Active database connections count"))
        self.register(CounterMetric("redis_governance_operations_total", "Total distributed coordination operations"))
        self.register(GaugeMetric("redis_fallback_active", "Flag indicating whether shared governance is currently in memory fallback mode (1=active, 0=normal)"))

        # Circuit Breakers & Resource Governance
        self.register(GaugeMetric("circuit_breaker_state", "Circuit breaker state per service (0=CLOSED, 1=HALF_OPEN, 2=OPEN)"))
        self.register(CounterMetric("circuit_breaker_short_circuits_total", "Total requests rejected immediately by open circuit breakers"))
        self.register(GaugeMetric("resource_budget_utilization_ratio", "Current consumption ratio of configured resource budget ceiling (0.0 to 1.0)"))

    def register(self, metric: Union[CounterMetric, GaugeMetric, HistogramMetric]) -> Any:
        with self._lock:
            self._metrics[metric.name] = metric
            return metric

    def get_counter(self, name: str) -> Optional[CounterMetric]:
        with self._lock:
            m = self._metrics.get(name)
            return m if isinstance(m, CounterMetric) else None

    def get_gauge(self, name: str) -> Optional[GaugeMetric]:
        with self._lock:
            m = self._metrics.get(name)
            return m if isinstance(m, GaugeMetric) else None

    def get_histogram(self, name: str) -> Optional[HistogramMetric]:
        with self._lock:
            m = self._metrics.get(name)
            return m if isinstance(m, HistogramMetric) else None

    def get_all_metrics(self) -> Dict[str, Any]:
        """Return hierarchical snapshot of all registered metrics."""
        with self._lock:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metrics": {name: m.to_dict() for name, m in self._metrics.items()},
            }

    def generate_prometheus_text(self) -> str:
        """Export all metrics formatted according to Prometheus exposition standard."""
        lines: List[str] = []
        with self._lock:
            for name, m in sorted(self._metrics.items()):
                lines.append(f"# HELP {m.name} {m.description}")
                lines.append(f"# TYPE {m.name} {m.__class__.__name__.replace('Metric', '').lower()}")

                if isinstance(m, CounterMetric):
                    for key, count in m._counts.items():
                        labels_str = f"{{{key}}}" if key else ""
                        lines.append(f"{m.name}{labels_str} {count}")

                elif isinstance(m, GaugeMetric):
                    for key, val in m._values.items():
                        labels_str = f"{{{key}}}" if key else ""
                        lines.append(f"{m.name}{labels_str} {val}")

                elif isinstance(m, HistogramMetric):
                    now = time.time()
                    for key in m._samples.keys():
                        base_labels = key
                        # Buckets
                        cum_count = 0
                        for b in m.buckets:
                            b_count = m._bucket_counts[key].get(b, 0)
                            lbl_part = f'{base_labels},le="{b}"' if base_labels else f'le="{b}"'
                            lines.append(f'{m.name}_bucket{{{lbl_part}}} {b_count}')

                        # +Inf bucket
                        tot_count = m._total_counts.get(key, 0)
                        lbl_inf = f'{base_labels},le="+Inf"' if base_labels else 'le="+Inf"'
                        lines.append(f'{m.name}_bucket{{{lbl_inf}}} {tot_count}')

                        # Count & Sum
                        lbl_base = f"{{{base_labels}}}" if base_labels else ""
                        lines.append(f"{m.name}_count{lbl_base} {tot_count}")
                        lines.append(f"{m.name}_sum{lbl_base} {m._total_sums.get(key, 0.0):.2f}")

                lines.append("")
        return "\n".join(lines)

    def reset_all(self) -> None:
        """Reset all metrics (for testing)."""
        with self._lock:
            for m in self._metrics.values():
                m.reset()


# Global Registry Singleton
platform_metrics = MetricsRegistry()
