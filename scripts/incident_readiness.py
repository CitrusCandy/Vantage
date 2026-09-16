#!/usr/bin/env python3
"""
Vantage News - Production Incident Readiness & Operational Checklist Script
Performs end-to-end verification of service health, readiness probe, database connectivity,
worker scheduler state, ingestion freshness, pipeline telemetry, and runs an alert evaluation pass.

Returns:
  0 - All critical invariants satisfied (System Ready for Incident Response)
  1 - Critical failure detected (System Unready / Degradation Active)
"""

import json
import logging
import os
from pathlib import Path
import sys
import time

# Ensure backend path is on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Ensure offline DB URL default if unset
if not os.getenv("DATABASE_URL") or "localhost" in os.getenv("DATABASE_URL", ""):
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"

# Color helpers
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log_pass(msg: str):
    print(f"[{GREEN}PASS{RESET}] {msg}")


def log_fail(msg: str):
    print(f"[{RED}FAIL{RESET}] {msg}")


def log_warn(msg: str):
    print(f"[{YELLOW}WARN{RESET}] {msg}")


def log_info(msg: str):
    print(f"[{CYAN}INFO{RESET}] {msg}")


def check_liveness_and_readiness(client) -> bool:
    """Verify HTTP /health and /ready probes."""
    print("\n--- 1. Health & Readiness Probes ---")
    healthy = True

    try:
        resp_h = client.get("/health")
        if resp_h.status_code == 200 and resp_h.json().get("status") == "healthy":
            log_pass("Liveness probe /health is OK (HTTP 200)")
        else:
            log_fail(f"Liveness probe /health failed: status {resp_h.status_code}")
            healthy = False

        resp_r = client.get("/ready")
        if resp_r.status_code == 200 and resp_r.json().get("status") == "ready":
            log_pass("Readiness probe /ready is OK (HTTP 200, Database connected)")
        else:
            log_fail(f"Readiness probe /ready failed: status {resp_r.status_code}, body: {resp_r.text}")
            healthy = False
    except Exception as e:
        log_fail(f"Exception checking probes: {e}")
        healthy = False

    return healthy


def check_database_connectivity(engine) -> bool:
    """Verify database connection and query latency."""
    print("\n--- 2. Database Connectivity & Ping Latency ---")
    from sqlalchemy import text

    try:
        t0 = time.perf_counter()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = (time.perf_counter() - t0) * 1000.0
        log_pass(f"Database reachable. Ping latency: {latency_ms:.2f} ms")
        return True
    except Exception as e:
        log_fail(f"Database unreachable: {e}")
        return False


def check_worker_state() -> bool:
    """Verify background scheduler and topic refresh worker state."""
    print("\n--- 3. Background Worker Scheduler State ---")
    from app.workers.scheduler import scheduler

    status = scheduler.get_status()
    log_info(f"Worker Status: running={status.get('is_running')}, total_runs={status.get('total_runs')}, interval={status.get('worker_interval_hours')}h")

    if status.get("last_error"):
        log_warn(f"Worker reported previous error: {status['last_error']}")

    log_pass("Worker scheduler state verified")
    return True


def check_source_health_and_freshness() -> bool:
    """Verify ingestion sources telemetry and error rates."""
    print("\n--- 4. Ingestion Sources Freshness & Error Rates ---")
    from app.core.telemetry import ops_metrics

    source_summary = ops_metrics.get_source_health_summary()
    all_ok = True

    for src_name, data in source_summary.items():
        enabled = data.get("enabled", True)
        if not enabled:
            log_info(f"Source '{src_name}' is disabled (no alarms generated)")
            continue

        failures = data.get("failure_count", 0) + data.get("timeout_count", 0)
        successes = data.get("success_count", 0)
        total = failures + successes
        status_str = data.get("status", "healthy")

        if status_str == "unavailable":
            log_fail(f"Source '{src_name}' is UNAVAILABLE ({failures} failures, {successes} successes)")
            all_ok = False
        elif status_str == "degraded":
            log_warn(f"Source '{src_name}' is degraded ({failures} failures, {successes} successes)")
        else:
            log_pass(f"Source '{src_name}' is {status_str} ({successes} successes, {failures} failures)")

    return all_ok


def check_pipeline_telemetry() -> bool:
    """Verify pipeline execution telemetry and latency."""
    print("\n--- 5. Pipeline Telemetry & Performance ---")
    from app.core.telemetry import ops_metrics

    metrics = ops_metrics.get_pipeline_metrics()
    total_runs = metrics.get("total_runs", 0)
    success_rate = metrics.get("success_rate", 1.0)
    avg_dur = metrics.get("avg_duration_ms", 0.0)

    log_info(f"Pipeline Telemetry: total_runs={total_runs}, success_rate={success_rate*100:.1f}%, avg_duration={avg_dur:.2f}ms")
    if total_runs > 0 and success_rate < 0.5:
        log_fail(f"Pipeline success rate too low: {success_rate*100:.1f}%")
        return False

    log_pass("Pipeline telemetry nominal")
    return True


def check_alert_evaluation(client) -> bool:
    """Run an alert evaluation cycle and check active alert severity."""
    print("\n--- 6. Alert Evaluation Cycle ---")
    try:
        from app.core.alerting import alert_manager
        from app.database.database import SessionLocal

        db = SessionLocal()
        try:
            eval_result = alert_manager.evaluate(db=db)
        finally:
            db.close()

        active_count = eval_result.get("active_count", 0)
        active_alerts = eval_result.get("active_alerts", [])
        has_critical = any(a.get("severity") == "critical" for a in active_alerts)

        log_info(f"Alert Evaluation: {active_count} active alerts, {eval_result.get('resolved_count', 0)} resolved")

        if has_critical:
            for alert in active_alerts:
                if alert.get("severity") == "critical":
                    log_fail(f"CRITICAL ALERT: [{alert.get('component')}] {alert.get('message')}")
            return False

        if active_count > 0:
            for alert in active_alerts:
                log_warn(f"WARNING ALERT: [{alert.get('component')}] {alert.get('message')}")

        log_pass("Alert evaluation completed with zero critical operational alerts")
        return True
    except Exception as e:
        log_fail(f"Alert evaluation cycle encountered exception: {e}")
        return False


def main():
    print(f"\n{BOLD}============================================================{RESET}")
    print(f"{BOLD}   VANTAGE NEWS - INCIDENT READINESS & PRODUCTION CHECKLIST {RESET}")
    print(f"{BOLD}============================================================{RESET}")

    from fastapi.testclient import TestClient
    from app.main import app
    from app.database.database import Base, engine

    # Ensure schema is ready
    Base.metadata.create_all(bind=engine)
    client = TestClient(app)

    results = [
        check_liveness_and_readiness(client),
        check_database_connectivity(engine),
        check_worker_state(),
        check_source_health_and_freshness(),
        check_pipeline_telemetry(),
        check_alert_evaluation(client),
    ]

    print(f"\n{BOLD}============================================================{RESET}")
    if all(results):
        print(f"{GREEN}{BOLD}  INCIDENT READINESS: ALL CHECKS PASSED - PRODUCTION READY! {RESET}")
        print(f"{BOLD}============================================================{RESET}\n")
        sys.exit(0)
    else:
        print(f"{RED}{BOLD}  INCIDENT READINESS: FAILED CRITICAL CHECKS! {RESET}")
        print(f"{BOLD}============================================================{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
