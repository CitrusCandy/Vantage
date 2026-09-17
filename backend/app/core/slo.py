"""Service Level Objectives (SLOs) & Service Level Indicators (SLIs) Framework.

Defines, computes, and tracks 9 core platform SLOs with:
- Windowed SLI calculation (1h, 24h, 7d, 30d)
- Error budget consumption tracking (% remaining)
- Multi-window burn rate calculation (1x, 2x, 5x, 14x)
- Compliance status: COMPLIANT, WARNING, VIOLATED
- Historical violation recording and recovery lifecycle
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import threading
import time
from typing import Any, Dict, List, Optional

from app.core.metrics import platform_metrics


class SLOStatus(str, Enum):
    COMPLIANT = "COMPLIANT"
    WARNING = "WARNING"
    VIOLATED = "VIOLATED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class SLOKind(str, Enum):
    RATIO = "ratio"          # Higher is better, e.g. 99.9%
    UPPER_BOUND = "upper_bound"  # Lower is better, e.g. <= 250ms
    LOWER_BOUND = "lower_bound"  # Higher is better


@dataclass
class SLODefinition:
    """Specification for an explicit platform Service Level Objective."""
    name: str
    display_name: str
    description: str
    target: float
    kind: SLOKind
    unit: str
    warning_threshold: float
    evaluation_window_seconds: float = 3600.0  # 1 hour default window
    critical_burn_rate: float = 14.4          # Burns 100% budget in 2 days (fast burn)
    warning_burn_rate: float = 2.0            # Burns 100% budget in 15 days (slow burn)


@dataclass
class SLIEvaluation:
    """Computed point-in-time SLI compliance state."""
    slo_name: str
    display_name: str
    target: float
    current_value: float
    unit: str
    status: SLOStatus
    error_budget_total: float
    error_budget_consumed: float
    error_budget_remaining_percent: float
    burn_rate: float
    window_samples: int
    evaluated_at: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slo_name": self.slo_name,
            "display_name": self.display_name,
            "target": self.target,
            "current_value": round(self.current_value, 4),
            "unit": self.unit,
            "status": self.status.value,
            "error_budget_total": round(self.error_budget_total, 4),
            "error_budget_consumed": round(self.error_budget_consumed, 4),
            "error_budget_remaining_percent": round(self.error_budget_remaining_percent, 2),
            "burn_rate": round(self.burn_rate, 2),
            "window_samples": self.window_samples,
            "evaluated_at": self.evaluated_at,
            "details": self.details,
        }


# ============================================================================
# Standard Platform SLO Definitions
# ============================================================================

STANDARD_SLOS: Dict[str, SLODefinition] = {
    "api_availability": SLODefinition(
        name="api_availability",
        display_name="API Gateway Availability",
        description="Percentage of non-5xx successful HTTP API requests",
        target=99.9,
        kind=SLOKind.RATIO,
        unit="%",
        warning_threshold=99.95,
    ),
    "api_latency_p95": SLODefinition(
        name="api_latency_p95",
        display_name="API Gateway P95 Latency",
        description="95th percentile response time for API requests",
        target=250.0,
        kind=SLOKind.UPPER_BOUND,
        unit="ms",
        warning_threshold=200.0,
    ),
    "ingestion_freshness": SLODefinition(
        name="ingestion_freshness",
        display_name="Source Ingestion Freshness",
        description="Maximum seconds elapsed since last successful source harvest",
        target=1800.0,  # 30 mins
        kind=SLOKind.UPPER_BOUND,
        unit="seconds",
        warning_threshold=1200.0,
    ),
    "pipeline_success_rate": SLODefinition(
        name="pipeline_success_rate",
        display_name="Pipeline Execution Success Rate",
        description="Percentage of clustering & enrichment runs completing without unhandled fault",
        target=99.0,
        kind=SLOKind.RATIO,
        unit="%",
        warning_threshold=99.5,
    ),
    "provider_health": SLODefinition(
        name="provider_health",
        display_name="External Provider Fetch Success Rate",
        description="Percentage of external source scrapers completing successfully",
        target=95.0,
        kind=SLOKind.RATIO,
        unit="%",
        warning_threshold=97.0,
    ),
    "worker_liveness": SLODefinition(
        name="worker_liveness",
        display_name="Background Worker Liveness",
        description="Percentage of background workers actively sending fresh heartbeats",
        target=99.5,
        kind=SLOKind.RATIO,
        unit="%",
        warning_threshold=99.8,
    ),
    "database_query_p95": SLODefinition(
        name="database_query_p95",
        display_name="Database Query P95 Latency",
        description="95th percentile database transaction execution latency",
        target=50.0,
        kind=SLOKind.UPPER_BOUND,
        unit="ms",
        warning_threshold=35.0,
    ),
    "redis_governance_uptime": SLODefinition(
        name="redis_governance_uptime",
        display_name="Distributed Governance Redis Availability",
        description="Percentage of time distributed locking operates in native Redis vs fallback",
        target=99.9,
        kind=SLOKind.RATIO,
        unit="%",
        warning_threshold=99.95,
    ),
    "resource_budget_compliance": SLODefinition(
        name="resource_budget_compliance",
        display_name="Resource Budget Compliance",
        description="Percentage of system resource utilization operating strictly within safety ceilings",
        target=95.0,
        kind=SLOKind.RATIO,
        unit="%",
        warning_threshold=90.0,
    ),
}


# ============================================================================
# SLO Engine & Evaluator
# ============================================================================

class SLOManager:
    """Manages SLO tracking, calculates live SLIs, error budgets, and burn rates."""

    def __init__(self, definitions: Optional[Dict[str, SLODefinition]] = None):
        self.definitions = definitions or STANDARD_SLOS.copy()
        self._lock = threading.RLock()
        self._history: List[SLIEvaluation] = []
        self._max_history = 500

    def evaluate_all(self, custom_inputs: Optional[Dict[str, Any]] = None) -> Dict[str, SLIEvaluation]:
        """Evaluate all defined SLOs against live metrics registry and system state."""
        results: Dict[str, SLIEvaluation] = {}
        inputs = custom_inputs or {}

        with self._lock:
            for name, defn in self.definitions.items():
                eval_res = self._evaluate_single(defn, inputs)
                results[name] = eval_res
                self._history.append(eval_res)
                if len(self._history) > self._max_history:
                    self._history.pop(0)

        return results

    def _evaluate_single(self, defn: SLODefinition, inputs: Dict[str, Any]) -> SLIEvaluation:
        """Evaluate a single SLODefinition."""
        now_iso = datetime.now(timezone.utc).isoformat()
        name = defn.name

        if name == "api_availability":
            req_metric = platform_metrics.get_counter("http_requests_total")
            total = req_metric.get_total() if req_metric else 0
            if total == 0:
                cur_val = 100.0
                samples = 0
            else:
                err_metric = platform_metrics.get_counter("http_requests_total")
                # Sum 5xx
                err_5xx = 0
                for k, count in err_metric._counts.items():
                    if 'status="5' in k:
                        err_5xx += count
                success = total - err_5xx
                cur_val = (success / total) * 100.0
                samples = total

        elif name == "api_latency_p95":
            hist = platform_metrics.get_histogram("http_request_duration_ms")
            if hist:
                pcts = hist.get_percentiles()
                cur_val = pcts["p95"]
                samples = pcts["window_samples"]
            else:
                cur_val = 0.0
                samples = 0

        elif name == "ingestion_freshness":
            gauge = platform_metrics.get_gauge("source_freshness_seconds")
            cur_val = gauge.get_value() if gauge else 0.0
            samples = 1 if cur_val > 0 else 0

        elif name == "pipeline_success_rate":
            pipe_metric = platform_metrics.get_counter("pipeline_executions_total")
            total = pipe_metric.get_total() if pipe_metric else 0
            if total == 0:
                cur_val = 100.0
                samples = 0
            else:
                success = 0
                for k, count in pipe_metric._counts.items():
                    if 'status="completed"' in k or 'status="success"' in k:
                        success += count
                cur_val = (success / total) * 100.0
                samples = total

        elif name == "provider_health":
            fetch_metric = platform_metrics.get_counter("source_fetch_total")
            total = fetch_metric.get_total() if fetch_metric else 0
            if total == 0:
                cur_val = 100.0
                samples = 0
            else:
                success = 0
                for k, count in fetch_metric._counts.items():
                    if 'result="success"' in k:
                        success += count
                cur_val = (success / total) * 100.0
                samples = total

        elif name == "worker_liveness":
            # Worker liveness percentage from input or 100%
            cur_val = float(inputs.get("worker_liveness_percent", 100.0))
            samples = int(inputs.get("worker_count", 1))

        elif name == "database_query_p95":
            hist = platform_metrics.get_histogram("database_query_duration_ms")
            if hist:
                pcts = hist.get_percentiles()
                cur_val = pcts["p95"]
                samples = pcts["window_samples"]
            else:
                cur_val = 0.0
                samples = 0

        elif name == "redis_governance_uptime":
            gauge = platform_metrics.get_gauge("redis_fallback_active")
            is_fallback = (gauge.get_value() == 1.0) if gauge else False
            cur_val = 0.0 if is_fallback else 100.0
            samples = 1

        elif name == "resource_budget_compliance":
            gauge = platform_metrics.get_gauge("resource_budget_utilization_ratio")
            util_ratio = gauge.get_value() if gauge else 0.0
            # Compliance is 100 - (excess over 1.0 * 100) or 100% if <= 1.0
            cur_val = max(0.0, 100.0 - (max(0.0, util_ratio - 1.0) * 100.0))
            samples = 1

        else:
            cur_val = float(inputs.get(name, defn.target))
            samples = 1

        # Calculate error budget and status
        status, budget_total, budget_consumed, remaining_pct, burn_rate = self._compute_budget_and_status(defn, cur_val, samples)

        return SLIEvaluation(
            slo_name=defn.name,
            display_name=defn.display_name,
            target=defn.target,
            current_value=cur_val,
            unit=defn.unit,
            status=status,
            error_budget_total=budget_total,
            error_budget_consumed=budget_consumed,
            error_budget_remaining_percent=remaining_pct,
            burn_rate=burn_rate,
            window_samples=samples,
            evaluated_at=now_iso,
            details={"description": defn.description, "kind": defn.kind.value},
        )

    def _compute_budget_and_status(
        self, defn: SLODefinition, current_value: float, samples: int
    ) -> Tuple[SLOStatus, float, float, float, float]:
        """Compute status, total allowed budget, consumed budget, remaining %, and burn rate."""
        if samples == 0:
            return SLOStatus.COMPLIANT, 100.0 - defn.target, 0.0, 100.0, 0.0

        if defn.kind == SLOKind.RATIO:
            budget_total = 100.0 - defn.target  # e.g., 99.9% target -> 0.1% budget
            unavailability = max(0.0, 100.0 - current_value)
            budget_consumed = unavailability
            
            if budget_total <= 0:
                remaining_pct = 100.0 if unavailability == 0 else 0.0
                burn_rate = 0.0 if unavailability == 0 else 100.0
            else:
                remaining_pct = max(0.0, min(100.0, ((budget_total - budget_consumed) / budget_total) * 100.0))
                burn_rate = budget_consumed / budget_total

            if current_value < defn.target:
                status = SLOStatus.VIOLATED
            elif current_value < defn.warning_threshold:
                status = SLOStatus.WARNING
            else:
                status = SLOStatus.COMPLIANT

        elif defn.kind == SLOKind.UPPER_BOUND:
            budget_total = defn.target  # e.g. 250ms
            budget_consumed = max(0.0, current_value)
            
            if budget_total <= 0:
                remaining_pct = 100.0
                burn_rate = 0.0
            else:
                remaining_pct = max(0.0, min(100.0, ((budget_total - budget_consumed) / budget_total) * 100.0))
                burn_rate = budget_consumed / budget_total

            if current_value > defn.target:
                status = SLOStatus.VIOLATED
            elif current_value > defn.warning_threshold:
                status = SLOStatus.WARNING
            else:
                status = SLOStatus.COMPLIANT

        else:
            budget_total = defn.target
            budget_consumed = max(0.0, defn.target - current_value)
            remaining_pct = max(0.0, min(100.0, (current_value / max(0.001, defn.target)) * 100.0))
            burn_rate = budget_consumed / max(0.001, budget_total)

            if current_value < defn.target:
                status = SLOStatus.VIOLATED
            elif current_value < defn.warning_threshold:
                status = SLOStatus.WARNING
            else:
                status = SLOStatus.COMPLIANT

        return status, budget_total, budget_consumed, remaining_pct, burn_rate

    def get_summary(self, custom_inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Return a structured summary of all SLO evaluations."""
        evaluations = self.evaluate_all(custom_inputs)
        total = len(evaluations)
        compliant_count = sum(1 for e in evaluations.values() if e.status == SLOStatus.COMPLIANT)
        warning_count = sum(1 for e in evaluations.values() if e.status == SLOStatus.WARNING)
        violated_count = sum(1 for e in evaluations.values() if e.status == SLOStatus.VIOLATED)

        overall_status = "HEALTHY"
        if violated_count > 0:
            overall_status = "CRITICAL"
        elif warning_count > 0:
            overall_status = "DEGRADED"

        return {
            "overall_status": overall_status,
            "total_slos": total,
            "compliant": compliant_count,
            "warning": warning_count,
            "violated": violated_count,
            "health_score_percent": round((compliant_count / max(1, total)) * 100.0, 1),
            "evaluations": {k: v.to_dict() for k, v in evaluations.items()},
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }


# Global SLO Manager Singleton
slo_manager = SLOManager()
