"""Operational History Cleanup and Retention Maintenance Script.

Prunes expired operational telemetry records (pipeline_runs, source_executions,
worker_cycles, and resolved operational_alerts) to enforce data retention limits.

Usage:
    python -m app.database.cleanup_ops_history [--days 30] [--alerts-days 90] [--dry-run]
"""

import argparse
from datetime import datetime, timedelta
import logging
import os
import sys
from typing import Any, Dict, Optional

from sqlalchemy import and_

from app.database.database import SessionLocal
from app.database.models import OperationalAlert, PipelineRun, SourceExecution, WorkerCycle

logger = logging.getLogger("app.database.cleanup_ops_history")


def cleanup_ops_history(
    ops_retention_days: int = 30,
    alert_retention_days: int = 90,
    dry_run: bool = False,
    db: Optional[Any] = None,
) -> Dict[str, Any]:
    """Prune historical operational data older than retention thresholds.

    Active operational alerts are NEVER pruned to ensure unresolved issues remain visible.
    Only resolved alerts older than alert_retention_days are purged.

    Args:
        ops_retention_days: Max age in days for pipeline runs, source executions, and worker cycles.
        alert_retention_days: Max age in days for resolved alerts.
        dry_run: If True, count candidate rows without deleting.
        db: Optional database session (used in testing).

    Returns:
        Dict summarizing deleted/candidate record counts per table.
    """
    now = datetime.utcnow()
    ops_cutoff = now - timedelta(days=ops_retention_days)
    alert_cutoff = now - timedelta(days=alert_retention_days)

    db_sess = db if db is not None else SessionLocal()
    should_close = db is None
    summary = {
        "executed_at": now.isoformat(),
        "ops_retention_days": ops_retention_days,
        "alert_retention_days": alert_retention_days,
        "ops_cutoff": ops_cutoff.isoformat(),
        "alert_cutoff": alert_cutoff.isoformat(),
        "dry_run": dry_run,
        "pipeline_runs_pruned": 0,
        "source_executions_pruned": 0,
        "worker_cycles_pruned": 0,
        "resolved_alerts_pruned": 0,
        "total_pruned": 0,
    }

    try:
        # 1. Pipeline Runs
        p_runs_q = db_sess.query(PipelineRun).filter(PipelineRun.started_at < ops_cutoff)
        summary["pipeline_runs_pruned"] = p_runs_q.count()

        # 2. Source Executions
        src_exec_q = db_sess.query(SourceExecution).filter(SourceExecution.started_at < ops_cutoff)
        summary["source_executions_pruned"] = src_exec_q.count()

        # 3. Worker Cycles
        worker_q = db_sess.query(WorkerCycle).filter(WorkerCycle.started_at < ops_cutoff)
        summary["worker_cycles_pruned"] = worker_q.count()

        # 4. Resolved Operational Alerts (Never prune active alerts)
        alerts_q = db_sess.query(OperationalAlert).filter(
            and_(
                OperationalAlert.status == "resolved",
                OperationalAlert.resolved_at < alert_cutoff,
            )
        )
        summary["resolved_alerts_pruned"] = alerts_q.count()

        summary["total_pruned"] = (
            summary["pipeline_runs_pruned"]
            + summary["source_executions_pruned"]
            + summary["worker_cycles_pruned"]
            + summary["resolved_alerts_pruned"]
        )

        if not dry_run and summary["total_pruned"] > 0:
            p_runs_q.delete(synchronize_session=False)
            src_exec_q.delete(synchronize_session=False)
            worker_q.delete(synchronize_session=False)
            alerts_q.delete(synchronize_session=False)
            db_sess.commit()
            logger.info(
                "Pruned %d operational records: %d pipeline runs, %d source executions, %d worker cycles, %d resolved alerts",
                summary["total_pruned"],
                summary["pipeline_runs_pruned"],
                summary["source_executions_pruned"],
                summary["worker_cycles_pruned"],
                summary["resolved_alerts_pruned"],
            )
        else:
            logger.info(
                "Cleanup check completed (dry_run=%s). Total candidates: %d",
                dry_run,
                summary["total_pruned"],
            )

        return summary
    except Exception as e:
        db_sess.rollback()
        logger.error("Operational history cleanup failed: %s", e)
        raise
    finally:
        if should_close:
            db_sess.close()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    default_ops_days = int(os.getenv("OPS_RETENTION_DAYS", "30"))
    default_alert_days = int(os.getenv("ALERT_RETENTION_DAYS", "90"))

    parser = argparse.ArgumentParser(
        description="Prune old operational history records from PostgreSQL database."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=default_ops_days,
        help=f"Retention period in days for pipeline, source, and worker telemetry (default: {default_ops_days})",
    )
    parser.add_argument(
        "--alerts-days",
        type=int,
        default=default_alert_days,
        help=f"Retention period in days for resolved operational alerts (default: {default_alert_days})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate cleanup and print candidate counts without deleting records",
    )

    args = parser.parse_args()

    try:
        res = cleanup_ops_history(
            ops_retention_days=args.days,
            alert_retention_days=args.alerts_days,
            dry_run=args.dry_run,
        )
        print("--- Operational History Retention Cleanup Summary ---")
        for k, v in res.items():
            print(f"  {k}: {v}")
    except Exception as err:
        print(f"Error during operational history cleanup: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
