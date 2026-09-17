from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.metrics import platform_metrics
from app.core.resilience import CircuitState, circuit_registry
from app.core.security import mask_sensitive_data
from app.core.slo import SLOStatus, slo_manager
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
    first_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
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

    @property
    def flapping_window_seconds(self) -> float:
        return float(os.getenv("ALERT_FLAPPING_WINDOW_SECONDS", "300.0"))

    @property
    def flapping_threshold_transitions(self) -> int:
        return int(os.getenv("ALERT_FLAPPING_THRESHOLD_TRANSITIONS", "4"))


class AlertManager:
    """Thread-safe centralized alert evaluation engine with deduplication, cooldown, flapping suppression, and lifecycle tracking."""

    def __init__(self, config: Optional[AlertConfig] = None):
        self._lock = threading.RLock()
        self.config = config or AlertConfig()
        self.active_alerts: Dict[str, AlertInstance] = {}
        self.resolved_history: deque = deque(maxlen=self.config.max_resolved_history)
        self.last_evaluation_time: Optional[str] = None
        self._loaded_from_db: bool = False
        # Flapping tracking: alert_id -> list of transition timestamps
        self._transition_history: Dict[str, deque] = {}

    def _is_flapping(self, alert_id: str, now_ts: float) -> bool:
        """Check if an alert is rapidly transitioning between states."""
        history = self._transition_history.get(alert_id)
        if not history:
            return False
        cutoff = now_ts - self.config.flapping_window_seconds
        while history and history[0] < cutoff:
            history.popleft()
        return len(history) >= self.config.flapping_threshold_transitions

    def _record_transition(self, alert_id: str, now_ts: float) -> None:
        if alert_id not in self._transition_history:
            self._transition_history[alert_id] = deque(maxlen=20)
        self._transition_history[alert_id].append(now_ts)

    def _ensure_loaded_from_db(self, db: Optional[Session] = None):
        """Restore active alerts and resolved history from database upon process restart."""
        if self._loaded_from_db:
            return

        try:
            from app.database.database import SessionLocal
            from app.database.models import OperationalAlert

            close_session = False
            session = db
            if session is None:
                session = SessionLocal()
                close_session = True

            try:
                # 1. Restore active alerts
                active_db = session.query(OperationalAlert).filter(OperationalAlert.status == "active").all()
                for item in active_db:
                    sev = AlertSeverity(item.severity) if item.severity in ("info", "warning", "critical") else AlertSeverity.WARNING
                    instance = AlertInstance(
                        id=item.alert_id,
                        rule_name=item.alert_type,
                        severity=sev,
                        component=item.component,
                        message=item.message,
                        status="active",
                        first_seen=item.first_seen.isoformat() if item.first_seen else datetime.now(timezone.utc).isoformat(),
                        last_seen=item.last_seen.isoformat() if item.last_seen else datetime.now(timezone.utc).isoformat(),
                        occurrence_count=item.occurrence_count,
                        metadata=item.metadata_json or {},
                    )
                    self.active_alerts[item.alert_id] = instance

                # 2. Restore recent resolved alerts
                resolved_db = (
                    session.query(OperationalAlert)
                    .filter(OperationalAlert.status == "resolved")
                    .order_by(OperationalAlert.resolved_at.desc())
                    .limit(self.config.max_resolved_history)
                    .all()
                )
                for item in reversed(resolved_db):
                    sev = AlertSeverity(item.severity) if item.severity in ("info", "warning", "critical") else AlertSeverity.WARNING
                    instance = AlertInstance(
                        id=item.alert_id,
                        rule_name=item.alert_type,
                        severity=sev,
                        component=item.component,
                        message=item.message,
                        status="resolved",
                        first_seen=item.first_seen.isoformat() if item.first_seen else datetime.now(timezone.utc).isoformat(),
                        last_seen=item.last_seen.isoformat() if item.last_seen else datetime.now(timezone.utc).isoformat(),
                        resolved_at=item.resolved_at.isoformat() if item.resolved_at else None,
                        occurrence_count=item.occurrence_count,
                        metadata=item.metadata_json or {},
                    )
                    self.resolved_history.appendleft(instance)

                self._loaded_from_db = True
            finally:
                if close_session:
                    session.close()
        except Exception as e:
            logger.warning("Could not restore alerts from database: %s", e)

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
        if event_type == "recovered" or event_type == "resolved":
            logger.info("[ALERT %s] %s", event_type.upper(), json.dumps(payload))
        else:
            logger.warning("[ALERT %s] %s", event_type.upper(), json.dumps(payload))

    def evaluate(self, db: Optional[Session] = None) -> Dict[str, Any]:
        """Perform a full alert evaluation cycle against current database, workers, pipeline, source telemetry, and SLOs."""
        if not self.config.enabled:
            return {
                "status": "disabled",
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
                "active_count": 0,
                "resolved_count": 0,
                "active_alerts": [],
                "resolved_alerts": [],
            }

        # Ensure active alerts from previous server runs are restored
        self._ensure_loaded_from_db(db)

        now_dt = datetime.now(timezone.utc)
        now_ts = time.time()
        now_iso = now_dt.isoformat()
        current_firing_keys: Set[str] = set()

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
                    now_ts=now_ts,
                )

            if db_connected and db_latency_ms > self.config.db_latency_threshold_ms:
                self._record_firing_alert(
                    rule_name="database_high_latency",
                    component="database",
                    severity=AlertSeverity.WARNING,
                    message=f"Database ping latency ({db_latency_ms}ms) exceeded threshold ({self.config.db_latency_threshold_ms}ms)",
                    metadata={"latency_ms": db_latency_ms, "threshold_ms": self.config.db_latency_threshold_ms},
                    firing_keys=current_firing_keys,
                    now_ts=now_ts,
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
                    now_ts=now_ts,
                )
            elif not is_running:
                self._record_firing_alert(
                    rule_name="worker_stopped",
                    component="worker",
                    severity=AlertSeverity.WARNING,
                    message="Background worker scheduler is inactive or stopped",
                    metadata={"is_running": False},
                    firing_keys=current_firing_keys,
                    now_ts=now_ts,
                )
            elif last_run_time_str:
                try:
                    last_run_dt = datetime.fromisoformat(last_run_time_str.replace("Z", "+00:00"))
                    if last_run_dt.tzinfo is None:
                        last_run_dt = last_run_dt.replace(tzinfo=timezone.utc)
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
                            now_ts=now_ts,
                        )
                except Exception as e:
                    logger.warning("Error parsing worker last_run_time: %s", e)

        # -------------------------------------------------------------
        # 3. Ingestion Sources Health & Stale Source Detection
        # -------------------------------------------------------------
        source_summary = ops_metrics.get_source_health_summary()
        for src_name, src_data in source_summary.items():
            if not src_data.get("enabled", True):
                continue

            failures = src_data.get("failure_count", 0) + src_data.get("timeout_count", 0)
            successes = src_data.get("success_count", 0)
            last_success_str = src_data.get("last_success")

            # A. Stale Source Detection
            if last_success_str:
                try:
                    last_succ_dt = datetime.fromisoformat(last_success_str.replace("Z", "+00:00"))
                    if last_succ_dt.tzinfo is None:
                        last_succ_dt = last_succ_dt.replace(tzinfo=timezone.utc)
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
                            now_ts=now_ts,
                        )
                except Exception as e:
                    logger.warning("Error parsing source last_success: %s", e)

            # B. Source Failure Spike
            if failures >= self.config.source_failure_threshold:
                sev = AlertSeverity.CRITICAL if src_name in ("google_news", "reddit") else AlertSeverity.WARNING
                self._record_firing_alert(
                    rule_name="source_failure_spike",
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
                    now_ts=now_ts,
                )

            # C. Specific Repeated LLM / X Failures
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
                    now_ts=now_ts,
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
                    now_ts=now_ts,
                )

        # -------------------------------------------------------------
        # 4. Multi-Stage Pipeline Telemetry & Latency
        # -------------------------------------------------------------
        pipe_metrics = ops_metrics.get_pipeline_metrics()
        recent_runs = pipe_metrics.get("recent_runs", [])
        if recent_runs:
            recent_fails = sum(1 for r in recent_runs[:5] if r.get("status") != "success")
            if recent_fails >= self.config.pipeline_failure_threshold:
                self._record_firing_alert(
                    rule_name="pipeline_failure_spike",
                    component="pipeline",
                    severity=AlertSeverity.CRITICAL,
                    message=f"Discourse pipeline experienced {recent_fails} failures in last {min(5, len(recent_runs))} runs",
                    metadata={"recent_failures": recent_fails, "sample_size": min(5, len(recent_runs))},
                    firing_keys=current_firing_keys,
                    now_ts=now_ts,
                )

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
                    now_ts=now_ts,
                )

        # -------------------------------------------------------------
        # 5. Circuit Breaker States
        # -------------------------------------------------------------
        cb_states = circuit_registry.get_all_status()
        for cb_name, cb_info in cb_states.items():
            if cb_info.get("state") == "OPEN":
                self._record_firing_alert(
                    rule_name="circuit_breaker_open",
                    component=f"circuit:{cb_name}",
                    severity=AlertSeverity.CRITICAL if "db" in cb_name or "openai" in cb_name else AlertSeverity.WARNING,
                    message=f"Circuit breaker for '{cb_name}' is OPEN. Requests are failing fast to protect dependency.",
                    metadata=cb_info,
                    firing_keys=current_firing_keys,
                    now_ts=now_ts,
                )

        # -------------------------------------------------------------
        # 6. Service Level Objectives (SLO) Violations & Burn Rates
        # -------------------------------------------------------------
        slo_evals = slo_manager.evaluate_all()
        slo_violations_to_persist = []

        for slo_name, slo_eval in slo_evals.items():
            if slo_eval.status == SLOStatus.VIOLATED:
                self._record_firing_alert(
                    rule_name=f"slo_violation_{slo_name}",
                    component=f"slo:{slo_name}",
                    severity=AlertSeverity.CRITICAL,
                    message=f"SLO '{slo_eval.display_name}' violated: current={slo_eval.current_value}{slo_eval.unit}, target={slo_eval.target}{slo_eval.unit} (burn_rate={slo_eval.burn_rate:.1f}x)",
                    metadata=slo_eval.to_dict(),
                    firing_keys=current_firing_keys,
                    now_ts=now_ts,
                )
                slo_violations_to_persist.append(slo_eval)

            elif slo_eval.status == SLOStatus.WARNING:
                self._record_firing_alert(
                    rule_name=f"slo_warning_{slo_name}",
                    component=f"slo:{slo_name}",
                    severity=AlertSeverity.WARNING,
                    message=f"SLO '{slo_eval.display_name}' approaching threshold: current={slo_eval.current_value}{slo_eval.unit}, target={slo_eval.target}{slo_eval.unit}",
                    metadata=slo_eval.to_dict(),
                    firing_keys=current_firing_keys,
                    now_ts=now_ts,
                )

        # -------------------------------------------------------------
        # 7. Resolve Any Alerts That Are No Longer Firing
        # -------------------------------------------------------------
        resolved_alerts_to_persist = []
        with self._lock:
            all_active_keys = list(self.active_alerts.keys())
            for key in all_active_keys:
                if key not in current_firing_keys:
                    alert = self.active_alerts.pop(key)
                    alert.status = "resolved"
                    alert.resolved_at = now_iso
                    self.resolved_history.appendleft(alert)
                    self._record_transition(key, now_ts)
                    self._log_structured_alert("recovered", alert)
                    resolved_alerts_to_persist.append(alert)

            self.last_evaluation_time = now_iso
            active_list = [a.to_dict() for a in self.active_alerts.values()]
            resolved_list = [a.to_dict() for a in list(self.resolved_history)]

        # Persist resolved alerts and SLO violations to DB
        if resolved_alerts_to_persist or slo_violations_to_persist:
            try:
                from app.database.database import SessionLocal
                from app.database.models import OperationalAlert, SLOViolationRecord

                db_sess = db if db is not None else SessionLocal()
                should_close = db is None
                try:
                    for r_alert in resolved_alerts_to_persist:
                        db_row = db_sess.query(OperationalAlert).filter(OperationalAlert.alert_id == r_alert.id).first()
                        if db_row:
                            db_row.status = "resolved"
                            db_row.resolved_at = now_dt

                    for s_eval in slo_violations_to_persist:
                        slo_rec = SLOViolationRecord(
                            slo_name=s_eval.slo_name,
                            status=s_eval.status.value,
                            target_value=s_eval.target,
                            actual_value=s_eval.current_value,
                            burn_rate=s_eval.burn_rate,
                            error_budget_remaining_percent=s_eval.error_budget_remaining_percent,
                            started_at=now_dt,
                            details_json=s_eval.to_dict(),
                        )
                        db_sess.add(slo_rec)

                    db_sess.commit()
                except Exception as db_err:
                    db_sess.rollback()
                    logger.warning("Failed to persist alerts/SLO records to DB: %s", db_err)
                finally:
                    if should_close:
                        db_sess.close()
            except Exception as e:
                logger.warning("DB sync skipped: %s", e)

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
        firing_keys: Set[str],
        now_ts: Optional[float] = None,
        db: Optional[Session] = None,
    ):
        """Thread-safe deduplication, cooldown registration, and database persistence for active alerts."""
        alert_id = f"{rule_name}:{component}"
        firing_keys.add(alert_id)
        current_ts = now_ts if now_ts is not None else time.time()
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        sanitized_msg = mask_sensitive_data(message)
        sanitized_meta = self._sanitize_metadata(metadata)
        target_instance = None

        with self._lock:
            if alert_id in self.active_alerts:
                existing = self.active_alerts[alert_id]
                existing.occurrence_count += 1
                existing.last_seen = now_iso
                existing.message = sanitized_msg
                existing.metadata.update(sanitized_meta)
                existing.severity = severity
                target_instance = existing
            else:
                self._record_transition(alert_id, now_ts)
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
                target_instance = new_alert

        # Persist active alert to database
        if target_instance:
            try:
                from app.database.database import SessionLocal
                from app.database.models import OperationalAlert

                db_sess = db if db is not None else SessionLocal()
                should_close = db is None
                try:
                    db_row = db_sess.query(OperationalAlert).filter(OperationalAlert.alert_id == alert_id).first()
                    sev_str = severity.value if isinstance(severity, AlertSeverity) else str(severity)
                    if db_row:
                        db_row.occurrence_count = target_instance.occurrence_count
                        db_row.last_seen = now_dt
                        db_row.message = sanitized_msg
                        db_row.metadata_json = sanitized_meta
                        db_row.severity = sev_str
                        db_row.status = "active"
                        db_row.resolved_at = None
                    else:
                        db_row = OperationalAlert(
                            alert_id=alert_id,
                            alert_type=rule_name,
                            severity=sev_str,
                            component=component,
                            status="active",
                            message=sanitized_msg,
                            occurrence_count=target_instance.occurrence_count,
                            first_seen=now_dt,
                            last_seen=now_dt,
                            metadata_json=sanitized_meta,
                        )
                        db_sess.add(db_row)
                    db_sess.commit()
                except Exception as db_err:
                    db_sess.rollback()
                    logger.warning("Failed to persist active alert to DB: %s", db_err)
                finally:
                    if should_close:
                        db_sess.close()
            except Exception as e:
                logger.warning("Active alert DB sync skipped: %s", e)

    def get_alerts_summary(self, db: Optional[Session] = None) -> Dict[str, Any]:
        """Return active alerts and recent resolved history without leaking credentials."""
        self._ensure_loaded_from_db(db)

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
            self._transition_history.clear()
            self._loaded_from_db = True


# Global singleton instance for alerting
alert_manager = AlertManager()
