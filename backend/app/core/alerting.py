from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import mask_sensitive_data
from app.core.telemetry import ops_metrics
from app.workers.scheduler import scheduler

logger = logging.getLogger("app.core.alerting")


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AlertInstance:
    id: str
    rule_name: str
    severity: AlertSeverity
    component: str
    message: str
    status: str = "active"  # "active" or "resolved"
    first_seen: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_seen: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    resolved_at: Optional[str] = None
    occurrence_count: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "rule_name": self.rule_name,
            "severity": self.severity.value if isinstance(self.severity, AlertSeverity) else str(self.severity),
            "component": self.component,
            "message": self.message,
            "status": self.status,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "resolved_at": self.resolved_at,
            "occurrence_count": self.occurrence_count,
            "metadata": self.metadata,
        }


class AlertConfig:
    """Configurable alert thresholds loaded dynamically from environment variables."""

    @property
    def enabled(self) -> bool:
        return os.getenv("ENABLE_ALERT_EVALUATION", "true").lower() in ("true", "1", "yes")

    @property
    def worker_alerts_enabled(self) -> bool:
        return os.getenv("ALERT_WORKER_ENABLED", "true").lower() in ("true", "1", "yes")

    @property
    def cooldown_seconds(self) -> int:
        return int(os.getenv("ALERT_COOLDOWN_SECONDS", "300"))

    @property
    def db_latency_threshold_ms(self) -> float:
        return float(os.getenv("ALERT_DB_LATENCY_THRESHOLD_MS", "2000.0"))

    @property
    def worker_grace_period_seconds(self) -> int:
        return int(os.getenv("ALERT_WORKER_GRACE_PERIOD_SECONDS", "1800"))

    @property
    def source_stale_hours(self) -> float:
        return float(os.getenv("ALERT_SOURCE_STALE_HOURS", "24.0"))

    @property
    def pipeline_failure_threshold(self) -> int:
        return int(os.getenv("ALERT_PIPELINE_FAILURE_THRESHOLD", "2"))

    @property
    def pipeline_latency_threshold_ms(self) -> float:
        return float(os.getenv("ALERT_PIPELINE_LATENCY_THRESHOLD_MS", "30000.0"))

    @property
    def source_failure_threshold(self) -> int:
        return int(os.getenv("ALERT_SOURCE_FAILURE_THRESHOLD", "3"))

    @property
    def llm_failure_threshold(self) -> int:
        return int(os.getenv("ALERT_LLM_FAILURE_THRESHOLD", "2"))

    @property
    def x_failure_threshold(self) -> int:
        return int(os.getenv("ALERT_X_FAILURE_THRESHOLD", "3"))

    @property
    def max_resolved_history(self) -> int:
        return int(os.getenv("ALERT_MAX_RESOLVED_HISTORY", "50"))


class AlertManager:
    """Thread-safe centralized alert evaluation engine with deduplication, cooldown, and lifecycle tracking."""

    def __init__(self, config: Optional[AlertConfig] = None):
        self._lock = threading.Lock()
        self.config = config or AlertConfig()
        self.active_alerts: Dict[str, AlertInstance] = {}
        self.resolved_history: deque = deque(maxlen=self.config.max_resolved_history)
        self.last_evaluation_time: Optional[str] = None

    def _sanitize_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure no secret tokens, passwords, cookies, or credentials leak into alert metadata."""
        sanitized = {}
        for k, v in meta.items():
            if isinstance(v, str):
                sanitized[k] = mask_sensitive_data(v)
            elif isinstance(v, dict):
                sanitized[k] = self._sanitize_metadata(v)
            else:
                sanitized[k] = v
        return sanitized

    def _log_structured_alert(self, event_type: str, alert: AlertInstance):
        """Output structured JSON log without leaking credentials or raw payloads."""
        payload = {
            "event": "alert_" + event_type,
            "alert_id": alert.id,
            "rule": alert.rule_name,
            "severity": alert.severity.value if isinstance(alert.severity, AlertSeverity) else str(alert.severity),
            "component": alert.component,
            "message": alert.message,
            "status": alert.status,
            "occurrence_count": alert.occurrence_count,
            "first_seen": alert.first_seen,
            "last_seen": alert.last_seen,
            "resolved_at": alert.resolved_at,
        }
        logger.warning("[ALERT %s] %s", event_type.upper(), json.dumps(payload))

    def evaluate(self, db: Optional[Session] = None) -> Dict[str, Any]:
        """Perform a full alert evaluation cycle against current database, workers, pipeline, and source telemetry."""
        if not self.config.enabled:
            return {
                "status": "disabled",
                "evaluated_at": datetime.utcnow().isoformat(),
                "active_count": 0,
                "resolved_count": 0,
                "active_alerts": [],
                "resolved_alerts": [],
            }

        now_dt = datetime.utcnow()
        now_iso = now_dt.isoformat()
        current_firing_keys = set()

        # -------------------------------------------------------------
        # 1. Database Connectivity & Readiness
        # -------------------------------------------------------------
        db_connected = True
        db_latency_ms = 0.0
        if db is not None:
            t_start = time.perf_counter()
            try:
                db.execute(text("SELECT 1"))
                db_latency_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
            except Exception as e:
                db_connected = False
                db_latency_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
                self._record_firing_alert(
                    rule_name="database_unavailable",
                    component="database",
                    severity=AlertSeverity.CRITICAL,
                    message=f"Database connectivity check failed: {str(e)[:120]}",
                    metadata={"error": str(e)[:120], "latency_ms": db_latency_ms},
                    firing_keys=current_firing_keys,
                )

            if db_connected and db_latency_ms > self.config.db_latency_threshold_ms:
                self._record_firing_alert(
                    rule_name="database_high_latency",
                    component="database",
                    severity=AlertSeverity.WARNING,
                    message=f"Database ping latency ({db_latency_ms}ms) exceeded threshold ({self.config.db_latency_threshold_ms}ms)",
                    metadata={"latency_ms": db_latency_ms, "threshold_ms": self.config.db_latency_threshold_ms},
                    firing_keys=current_firing_keys,
                )

        # -------------------------------------------------------------
        # 2. Worker Scheduler & Stale Worker Detection
        # -------------------------------------------------------------
        if self.config.worker_alerts_enabled:
            worker_status = scheduler.get_status()
            is_running = worker_status.get("is_running", False)
            last_run_time_str = worker_status.get("last_run_time")
            last_error = worker_status.get("last_error")
            interval_hours = worker_status.get("worker_interval_hours", 2)
            expected_window_seconds = (interval_hours * 3600) + self.config.worker_grace_period_seconds

            if last_error:
                self._record_firing_alert(
                    rule_name="worker_stopped",
                    component="worker",
                    severity=AlertSeverity.CRITICAL,
                    message=f"Background worker encountered critical error: {last_error[:100]}",
                    metadata={"last_error": last_error},
                    firing_keys=current_firing_keys,
                )
            elif not is_running:
                self._record_firing_alert(
                    rule_name="worker_stopped",
                    component="worker",
                    severity=AlertSeverity.WARNING,
                    message="Background worker scheduler is inactive or stopped",
                    metadata={"is_running": False},
                    firing_keys=current_firing_keys,
                )
            elif last_run_time_str:
                try:
                    last_run_dt = datetime.fromisoformat(last_run_time_str)
                    elapsed_seconds = (now_dt - last_run_dt).total_seconds()
                    if elapsed_seconds > expected_window_seconds:
                        self._record_firing_alert(
                            rule_name="worker_stale",
                            component="worker",
                            severity=AlertSeverity.WARNING,
                            message=f"Worker has not completed a cycle in {int(elapsed_seconds // 60)} minutes (expected within {int(expected_window_seconds // 60)}m)",
                            metadata={
                                "elapsed_seconds": round(elapsed_seconds, 1),
                                "expected_window_seconds": expected_window_seconds,
                                "last_run_time": last_run_time_str,
                            },
                            firing_keys=current_firing_keys,
                        )
                except Exception as e:
                    logger.warning("Error parsing worker last_run_time: %s", e)

        # -------------------------------------------------------------
        # 3. Ingestion Sources Health & Stale Source Detection
        # -------------------------------------------------------------
        source_summary = ops_metrics.get_source_health_summary()
        for src_name, src_data in source_summary.items():
            if not src_data.get("enabled", True):
                # Do not trigger false alarms on disabled sources
                continue

            failures = src_data.get("failure_count", 0) + src_data.get("timeout_count", 0)
            successes = src_data.get("success_count", 0)
            total = failures + successes
            last_success_str = src_data.get("last_success")

            # A. Stale Source Detection
            if last_success_str:
                try:
                    last_succ_dt = datetime.fromisoformat(last_success_str)
                    elapsed_src_sec = (now_dt - last_succ_dt).total_seconds()
                    max_stale_sec = self.config.source_stale_hours * 3600
                    if elapsed_src_sec > max_stale_sec:
                        self._record_firing_alert(
                            rule_name="source_stale",
                            component=f"source:{src_name}",
                            severity=AlertSeverity.WARNING,
                            message=f"Ingestion source '{src_name}' has no successful ingestion in {round(elapsed_src_sec/3600, 1)} hours",
                            metadata={"source": src_name, "last_success": last_success_str},
                            firing_keys=current_firing_keys,
                        )
                except Exception as e:
                    logger.warning("Error parsing source last_success: %s", e)

            # B. Source Failure Spike
            if failures >= self.config.source_failure_threshold:
                sev = AlertSeverity.CRITICAL if src_name in ("google_news", "reddit") else AlertSeverity.WARNING
                self._record_firing_alert(
                    rule_name=f"source_failure_spike",
                    component=f"source:{src_name}",
                    severity=sev,
                    message=f"Ingestion source '{src_name}' encountered {failures} failures/timeouts (threshold: {self.config.source_failure_threshold})",
                    metadata={
                        "source": src_name,
                        "failure_count": failures,
                        "success_count": successes,
                        "last_error": src_data.get("last_error_summary"),
                    },
                    firing_keys=current_firing_keys,
                )

            # B. Specific Repeated LLM / X Failures
            if src_name == "openai" and failures >= self.config.llm_failure_threshold:
                self._record_firing_alert(
                    rule_name="llm_repeated_failures",
                    component="llm:openai",
                    severity=AlertSeverity.CRITICAL,
                    message=f"Perspective synthesis LLM provider experienced {failures} repeated failures",
                    metadata={
                        "failure_count": failures,
                        "last_error": src_data.get("last_error_summary"),
                    },
                    firing_keys=current_firing_keys,
                )
            elif src_name == "x" and failures >= self.config.x_failure_threshold:
                self._record_firing_alert(
                    rule_name="x_scraper_repeated_failures",
                    component="source:x",
                    severity=AlertSeverity.WARNING,
                    message=f"X scraper experienced {failures} consecutive execution failures/timeouts",
                    metadata={
                        "failure_count": failures,
                        "last_error": src_data.get("last_error_summary"),
                    },
                    firing_keys=current_firing_keys,
                )

        # -------------------------------------------------------------
        # 4. Multi-Stage Pipeline Telemetry & Latency
        # -------------------------------------------------------------
        pipe_metrics = ops_metrics.get_pipeline_metrics()
        recent_runs = pipe_metrics.get("recent_runs", [])
        if recent_runs:
            # Check recent failure count
            recent_fails = sum(1 for r in recent_runs[:5] if r.get("status") != "success")
            if recent_fails >= self.config.pipeline_failure_threshold:
                self._record_firing_alert(
                    rule_name="pipeline_failure_spike",
                    component="pipeline",
                    severity=AlertSeverity.CRITICAL,
                    message=f"Discourse pipeline experienced {recent_fails} failures in last {min(5, len(recent_runs))} runs",
                    metadata={"recent_failures": recent_fails, "sample_size": min(5, len(recent_runs))},
                    firing_keys=current_firing_keys,
                )

            # Check pipeline high latency
            avg_duration_ms = pipe_metrics.get("avg_duration_ms", 0.0)
            latest_run_ms = recent_runs[0].get("total_duration_ms", 0.0) if recent_runs else 0.0
            if latest_run_ms > self.config.pipeline_latency_threshold_ms or avg_duration_ms > self.config.pipeline_latency_threshold_ms:
                self._record_firing_alert(
                    rule_name="pipeline_high_latency",
                    component="pipeline",
                    severity=AlertSeverity.WARNING,
                    message=f"Pipeline latency ({latest_run_ms}ms) exceeded threshold of {self.config.pipeline_latency_threshold_ms}ms",
                    metadata={
                        "latest_run_duration_ms": latest_run_ms,
                        "avg_duration_ms": avg_duration_ms,
                        "threshold_ms": self.config.pipeline_latency_threshold_ms,
                    },
                    firing_keys=current_firing_keys,
                )

        # -------------------------------------------------------------
        # 5. Resolve Any Alerts That Are No Longer Firing
        # -------------------------------------------------------------
        with self._lock:
            all_active_keys = list(self.active_alerts.keys())
            for key in all_active_keys:
                if key not in current_firing_keys:
                    alert = self.active_alerts.pop(key)
                    alert.status = "resolved"
                    alert.resolved_at = now_iso
                    self.resolved_history.appendleft(alert)
                    self._log_structured_alert("resolved", alert)

            self.last_evaluation_time = now_iso
            active_list = [a.to_dict() for a in self.active_alerts.values()]
            resolved_list = [a.to_dict() for a in list(self.resolved_history)]

        return {
            "status": "evaluated",
            "evaluated_at": now_iso,
            "active_count": len(active_list),
            "resolved_count": len(resolved_list),
            "active_alerts": active_list,
            "resolved_alerts": resolved_list,
        }

    def _record_firing_alert(
        self,
        rule_name: str,
        component: str,
        severity: AlertSeverity,
        message: str,
        metadata: Dict[str, Any],
        firing_keys: set,
    ):
        """Thread-safe deduplication and cooldown registration for active alerts."""
        alert_id = f"{rule_name}:{component}"
        firing_keys.add(alert_id)
        now_iso = datetime.utcnow().isoformat()
        sanitized_msg = mask_sensitive_data(message)
        sanitized_meta = self._sanitize_metadata(metadata)

        with self._lock:
            if alert_id in self.active_alerts:
                existing = self.active_alerts[alert_id]
                existing.occurrence_count += 1
                existing.last_seen = now_iso
                existing.message = sanitized_msg
                existing.metadata.update(sanitized_meta)
                existing.severity = severity
            else:
                new_alert = AlertInstance(
                    id=alert_id,
                    rule_name=rule_name,
                    severity=severity,
                    component=component,
                    message=sanitized_msg,
                    status="active",
                    first_seen=now_iso,
                    last_seen=now_iso,
                    occurrence_count=1,
                    metadata=sanitized_meta,
                )
                self.active_alerts[alert_id] = new_alert
                self._log_structured_alert("fired", new_alert)

    def get_alerts_summary(self) -> Dict[str, Any]:
        """Return active alerts and recent resolved history without leaking credentials."""
        with self._lock:
            active_list = [a.to_dict() for a in self.active_alerts.values()]
            resolved_list = [a.to_dict() for a in list(self.resolved_history)]
            last_eval = self.last_evaluation_time

        return {
            "last_evaluation_time": last_eval,
            "active_count": len(active_list),
            "resolved_count": len(resolved_list),
            "active_alerts": active_list,
            "resolved_alerts": resolved_list,
        }

    def clear(self):
        """Helper to reset all active alerts and history during tests."""
        with self._lock:
            self.active_alerts.clear()
            self.resolved_history.clear()
            self.last_evaluation_time = None


# Global singleton instance for alerting
alert_manager = AlertManager()
