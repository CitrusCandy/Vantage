from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import statistics
import threading
import time
from typing import Any, Dict, List, Optional

from app.core.security import mask_sensitive_data

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
        sample_size: int = 0,
        cluster_count: int = 0,
        perspective_count: int = 0,
        failure_stage: Optional[str] = None,
        error_type: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ):
        """Record a completed or failed pipeline execution into in-memory ring buffer and persistent DB."""
        ts = timestamp or datetime.utcnow()
        sanitized_error = mask_sensitive_data(error) if error else None
        sanitized_err_type = mask_sensitive_data(error_type) if error_type else None

        # Update structured platform metrics
        try:
            from app.core.metrics import platform_metrics
            p_counter = platform_metrics.get_counter("pipeline_executions_total")
            if p_counter:
                p_counter.inc(labels={"pipeline": pipeline_name, "status": status})
            p_hist = platform_metrics.get_histogram("pipeline_duration_ms")
            if p_hist:
                p_hist.observe(total_duration_ms, labels={"pipeline": pipeline_name})
            if stages_ms:
                s_hist = platform_metrics.get_histogram("pipeline_stage_duration_ms")
                if s_hist:
                    for s_name, s_ms in stages_ms.items():
                        s_hist.observe(float(s_ms), labels={"stage": s_name})
        except Exception as met_err:
            logger.debug("Structured metrics update skipped: %s", met_err)

        with self._lock:
            self.recent_runs.appendleft({
                "id": len(self.recent_runs) + 1,
                "pipeline_name": pipeline_name,
                "topic_slug": topic_slug or "general",
                "total_duration_ms": round(total_duration_ms, 2),
                "stages_ms": stages_ms,
                "status": status,
                "error": sanitized_error,
                "sample_size": sample_size,
                "cluster_count": cluster_count,
                "perspective_count": perspective_count,
                "failure_stage": failure_stage,
                "timestamp": ts.isoformat(),
            })

        # Graceful database persistence
        try:
            from app.database.database import SessionLocal
            from app.database.models import PipelineRun, Topic

            db = SessionLocal()
            try:
                topic_id = None
                if topic_slug:
                    t = db.query(Topic).filter(Topic.slug == topic_slug).first()
                    if t:
                        topic_id = t.id

                p_run = PipelineRun(
                    topic_id=topic_id,
                    topic_slug=topic_slug,
                    pipeline_type=pipeline_name,
                    status=status,
                    started_at=ts,
                    completed_at=datetime.utcnow(),
                    duration_ms=total_duration_ms,
                    sample_size=sample_size,
                    cluster_count=cluster_count,
                    perspective_count=perspective_count,
                    failure_stage=failure_stage,
                    error_type=sanitized_err_type or sanitized_error,
                    stages_ms=stages_ms,
                )
                db.add(p_run)
                db.commit()
            except Exception as db_err:
                db.rollback()
                logger.warning("Failed to persist PipelineRun to database: %s", db_err)
            finally:
                db.close()
        except Exception as outer_err:
            logger.warning("PipelineRun persistence skipped: %s", outer_err)

    def record_source_execution(
        self,
        source_name: str,
        success: bool,
        latency_ms: float,
        is_timeout: bool = False,
        error_summary: Optional[str] = None,
        operation: str = "fetch",
        item_count: int = 0,
    ):
        now_dt = datetime.utcnow()
        now_str = now_dt.isoformat()
        sanitized_err = mask_sensitive_data(error_summary) if error_summary else None
        status_val = "timeout" if is_timeout else ("success" if success else "failed")

        # Update structured platform metrics
        try:
            from app.core.metrics import platform_metrics
            s_counter = platform_metrics.get_counter("source_fetch_total")
            if s_counter:
                s_counter.inc(labels={"source": source_name, "result": status_val})
            s_hist = platform_metrics.get_histogram("source_fetch_duration_ms")
            if s_hist:
                s_hist.observe(latency_ms, labels={"source": source_name})
            if success:
                s_gauge = platform_metrics.get_gauge("source_freshness_seconds")
                if s_gauge:
                    s_gauge.set(0.0, labels={"source": source_name})
        except Exception as met_err:
            logger.debug("Structured source metrics update skipped: %s", met_err)

        with self._lock:
            if source_name not in self.source_health:
                self.source_health[source_name] = SourceHealthStatus(source_name=source_name, enabled=True)

            src = self.source_health[source_name]

            if is_timeout:
                src.timeout_count += 1
                src.last_failure = now_str
                src.last_error_summary = sanitized_err or "Request timed out"
            elif success:
                src.success_count += 1
                src.total_latency_ms += latency_ms
                src.last_success = now_str
            else:
                src.failure_count += 1
                src.last_failure = now_str
                src.last_error_summary = sanitized_err or "Execution failed"

        # Graceful database persistence
        try:
            from app.database.database import SessionLocal
            from app.database.models import SourceExecution

            db = SessionLocal()
            try:
                src_exec = SourceExecution(
                    source=source_name,
                    operation=operation,
                    status=status_val,
                    started_at=now_dt - timedelta(milliseconds=latency_ms),
                    completed_at=now_dt,
                    duration_ms=latency_ms,
                    item_count=item_count,
                    error_type=sanitized_err,
                )
                db.add(src_exec)
                db.commit()
            except Exception as db_err:
                db.rollback()
                logger.warning("Failed to persist SourceExecution to database: %s", db_err)
            finally:
                db.close()
        except Exception as outer_err:
            logger.warning("SourceExecution persistence skipped: %s", outer_err)

    def get_source_health_summary(self, db=None) -> Dict[str, Any]:
        with self._lock:
            result = {
                name: item.to_dict() for name, item in self.source_health.items()
            }

        # If in-memory counters are zero and db session is provided, populate from persisted executions
        if db is not None:
            try:
                from app.database.models import SourceExecution
                for src_name in list(result.keys()):
                    if result[src_name]["total_requests"] == 0:
                        total_db = db.query(SourceExecution).filter(SourceExecution.source == src_name).count()
                        if total_db > 0:
                            succ_db = db.query(SourceExecution).filter(
                                SourceExecution.source == src_name,
                                SourceExecution.status == "success",
                            ).count()
                            fail_db = db.query(SourceExecution).filter(
                                SourceExecution.source == src_name,
                                SourceExecution.status != "success",
                            ).count()
                            last_succ = db.query(SourceExecution).filter(
                                SourceExecution.source == src_name,
                                SourceExecution.status == "success",
                            ).order_by(SourceExecution.started_at.desc()).first()

                            result[src_name]["total_requests"] = total_db
                            result[src_name]["success_count"] = succ_db
                            result[src_name]["failure_count"] = fail_db
                            if last_succ and last_succ.started_at:
                                result[src_name]["last_success"] = last_succ.started_at.isoformat()
                            result[src_name]["status"] = (
                                "healthy" if fail_db == 0 else ("degraded" if succ_db > fail_db else "unavailable")
                            )
            except Exception as e:
                logger.warning("Error reading persisted source execution stats: %s", e)

        return result

    def get_pipeline_metrics(self, db=None) -> Dict[str, Any]:
        with self._lock:
            runs = list(self.recent_runs)

        if len(runs) == 0 and db is not None:
            try:
                from app.database.models import PipelineRun
                db_runs = (
                    db.query(PipelineRun)
                    .order_by(PipelineRun.started_at.desc())
                    .limit(20)
                    .all()
                )
                runs = [r.to_dict() for r in db_runs]
                # Format to match run dict schema
                for r in runs:
                    r["total_duration_ms"] = r.get("duration_ms", 0.0)
                    r["pipeline_name"] = r.get("pipeline_type", "discourse_pipeline")
                    r["timestamp"] = r.get("started_at")
            except Exception as e:
                logger.warning("Error reading persisted pipeline runs: %s", e)

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
