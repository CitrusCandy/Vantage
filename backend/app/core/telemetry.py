from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import logging
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

    def __init__(self, pipeline_name: str = "discourse_pipeline"):
        self.pipeline_name = pipeline_name
        self.timings: Dict[str, StageTiming] = {}
        self.start_time: float = time.perf_counter()
        self.end_time: Optional[float] = None

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

    def get_summary(self) -> Dict[str, Any]:
        """Return total elapsed time and dictionary of stage timings in milliseconds."""
        total_ms = (time.perf_counter() - self.start_time) * 1000.0
        return {
            "pipeline_name": self.pipeline_name,
            "total_duration_ms": round(total_ms, 2),
            "stages_ms": {
                name: round(t.duration_ms, 2) for name, t in self.timings.items()
            },
            "detailed": [t.to_dict() for t in self.timings.values()],
        }
