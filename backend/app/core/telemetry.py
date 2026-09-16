from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import logging
import statistics
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.core.telemetry")


@dataclass
class StageTiming:
    stage_name: str
    duration_ms: float
    started_at: float
    ended_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage_name": self.stage_name,
            "duration_ms": round(self.duration_ms, 2),
            "metadata": self.metadata,
        }


class PipelineTimingTracker:
    """Tracks and aggregates execution timings across multi-stage discourse pipelines."""

    def __init__(self, pipeline_name: str = "discourse_pipeline", topic_slug: Optional[str] = None):
        self.pipeline_name = pipeline_name
        self.topic_slug = topic_slug
        self.timings: Dict[str, StageTiming] = {}
        self.start_time: float = time.perf_counter()
        self.started_at_dt: datetime = datetime.utcnow()
        self.end_time: Optional[float] = None
        self.status: str = "running"
        self.error: Optional[str] = None

    @contextmanager
    def track(self, stage_name: str, **metadata: Any):
        """Context manager to measure and record execution time of a pipeline stage."""
        t_start = time.perf_counter()
        try:
            yield
        finally:
            t_end = time.perf_counter()
            duration_ms = (t_end - t_start) * 1000.0
            timing = StageTiming(
                stage_name=stage_name,
                duration_ms=duration_ms,
                started_at=t_start,
                ended_at=t_end,
                metadata=metadata,
            )
            self.timings[stage_name] = timing
            logger.info(
                "[%s] Stage '%s' completed in %.2f ms (metadata: %s)",
                self.pipeline_name,
                stage_name,
                duration_ms,
                metadata,
            )

    def record_manual(self, stage_name: str, duration_ms: float, **metadata: Any):
        """Record an externally measured stage timing."""
        now = time.perf_counter()
        self.timings[stage_name] = StageTiming(
            stage_name=stage_name,
            duration_ms=duration_ms,
            started_at=now - (duration_ms / 1000.0),
            ended_at=now,
            metadata=metadata,
        )

    def finish(self, status: str = "success", error: Optional[str] = None) -> Dict[str, Any]:
        """Mark pipeline as finished, compute total duration, and record into ops registry."""
        self.status = status
        self.error = error
        summary = self.get_summary()
        ops_metrics.record_pipeline_run(
            pipeline_name=self.pipeline_name,
            topic_slug=self.topic_slug,
            total_duration_ms=summary["total_duration_ms"],
            stages_ms=summary["stages_ms"],
            status=status,
            error=error,
            timestamp=self.started_at_dt,
        )
        return summary

    def get_summary(self) -> Dict[str, Any]:
        """Return total elapsed time and dictionary of stage timings in milliseconds."""
        total_ms = (time.perf_counter() - self.start_time) * 1000.0
        return {
            "pipeline_name": self.pipeline_name,
            "topic_slug": self.topic_slug,
            "status": self.status,
            "total_duration_ms": round(total_ms, 2),
            "stages_ms": {
                name: round(t.duration_ms, 2) for name, t in self.timings.items()
            },
            "detailed": [t.to_dict() for t in self.timings.values()],
        }


@dataclass
class SourceHealthStatus:
    source_name: str
    enabled: bool = True
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    total_latency_ms: float = 0.0
    last_success: Optional[str] = None
    last_failure: Optional[str] = None
    last_error_summary: Optional[str] = None

    @property
    def total_requests(self) -> int:
        return self.success_count + self.failure_count + self.timeout_count

    @property
    def avg_latency_ms(self) -> float:
        if self.success_count <= 0:
            return 0.0
        return round(self.total_latency_ms / self.success_count, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_name": self.source_name,
            "enabled": self.enabled,
            "status": "healthy" if self.failure_count == 0 else ("degraded" if self.success_count > self.failure_count else "unavailable"),
            "total_requests": self.total_requests,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "timeout_count": self.timeout_count,
            "avg_latency_ms": self.avg_latency_ms,
            "last_success": self.last_success,
            "last_failure": self.last_failure,
            "last_error_summary": self.last_error_summary,
        }


class OpsMetricsRegistry:
    """Thread-safe centralized telemetry registry for operational visibility."""

    def __init__(self, max_history: int = 100):
        self._lock = threading.Lock()
        self.max_history = max_history
        self.recent_runs: deque = deque(maxlen=max_history)
        self.source_health: Dict[str, SourceHealthStatus] = {
            "google_news": SourceHealthStatus(source_name="google_news", enabled=True),
            "reddit": SourceHealthStatus(source_name="reddit", enabled=True),
            "x": SourceHealthStatus(source_name="x", enabled=True),
            "openai": SourceHealthStatus(source_name="openai", enabled=True),
        }

    def record_pipeline_run(
        self,
        pipeline_name: str,
        topic_slug: Optional[str],
        total_duration_ms: float,
        stages_ms: Dict[str, float],
        status: str = "success",
        error: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ):
        with self._lock:
            self.recent_runs.appendleft({
                "id": len(self.recent_runs) + 1,
                "pipeline_name": pipeline_name,
                "topic_slug": topic_slug or "general",
                "total_duration_ms": round(total_duration_ms, 2),
                "stages_ms": stages_ms,
                "status": status,
                "error": error,
                "timestamp": (timestamp or datetime.utcnow()).isoformat(),
            })

    def record_source_execution(
        self,
        source_name: str,
        success: bool,
        latency_ms: float,
        is_timeout: bool = False,
        error_summary: Optional[str] = None,
    ):
        with self._lock:
            if source_name not in self.source_health:
                self.source_health[source_name] = SourceHealthStatus(source_name=source_name, enabled=True)

            src = self.source_health[source_name]
            now_str = datetime.utcnow().isoformat()

            if is_timeout:
                src.timeout_count += 1
                src.last_failure = now_str
                src.last_error_summary = error_summary or "Request timed out"
            elif success:
                src.success_count += 1
                src.total_latency_ms += latency_ms
                src.last_success = now_str
            else:
                src.failure_count += 1
                src.last_failure = now_str
                src.last_error_summary = error_summary or "Execution failed"

    def get_source_health_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                name: item.to_dict() for name, item in self.source_health.items()
            }

    def get_pipeline_metrics(self) -> Dict[str, Any]:
        with self._lock:
            runs = list(self.recent_runs)

        total_runs = len(runs)
        if total_runs == 0:
            return {
                "total_runs": 0,
                "success_count": 0,
                "failure_count": 0,
                "success_rate": 1.0,
                "avg_duration_ms": 0.0,
                "median_duration_ms": 0.0,
                "per_stage_avg_ms": {},
                "slowest_recent_stages": [],
                "recent_runs": [],
            }

        success_runs = [r for r in runs if r.get("status") == "success"]
        durations = [r["total_duration_ms"] for r in runs]
        avg_dur = round(sum(durations) / total_runs, 2)
        med_dur = round(statistics.median(durations), 2)

        # Stage aggregations
        stage_totals: Dict[str, List[float]] = {}
        for r in runs:
            for st_name, st_ms in r.get("stages_ms", {}).items():
                if st_name not in stage_totals:
                    stage_totals[st_name] = []
                stage_totals[st_name].append(st_ms)

        per_stage_avg = {
            st: round(sum(vals) / len(vals), 2) for st, vals in stage_totals.items()
        }

        # Sort stages by average duration descending
        slowest_stages = sorted(
            [{"stage": st, "avg_duration_ms": val} for st, val in per_stage_avg.items()],
            key=lambda x: x["avg_duration_ms"],
            reverse=True,
        )

        return {
            "total_runs": total_runs,
            "success_count": len(success_runs),
            "failure_count": total_runs - len(success_runs),
            "success_rate": round(len(success_runs) / total_runs, 4),
            "avg_duration_ms": avg_dur,
            "median_duration_ms": med_dur,
            "per_stage_avg_ms": per_stage_avg,
            "slowest_recent_stages": slowest_stages,
            "recent_runs": runs[:20],
        }


# Global singleton instance for operational observability
ops_metrics = OpsMetricsRegistry()
