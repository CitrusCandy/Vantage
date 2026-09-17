from datetime import datetime, timedelta
import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.alerting import alert_manager
from app.core.audit import record_audit_event
from app.core.metrics import platform_metrics
from app.core import resource_governor
from app.core.security import (
    SecurityRole,
    mask_sensitive_data,
    require_admin,
    require_operator,
    require_role,
    validate_slug,
)
from app.core.slo import slo_manager
from app.core.telemetry import PipelineTimingTracker, ops_metrics
from app.database.database import get_db
from app.database.models import SecurityAuditLog, Topic
from app.ingestion.pipeline import IngestionPipeline
from app.llm.pipeline import PerspectivePipeline
from app.processing.cluster_pipeline import ClusterPipeline
from app.workers.jobs import run_candidate_discovery_job, run_trending_refresh_job
from app.workers.scheduler import scheduler
from app.workers.topic_refresh import TopicRefreshWorker
from app.workers.trending import TrendingScorer

logger = logging.getLogger("app.api.ops")

router = APIRouter(prefix="/ops", tags=["Operations & Observability"])


def _enforce_ops_rate_limit(key: str):
    """Check rate limit for ops key and raise HTTP 429 if exceeded."""
    allowed, retry_after = resource_governor.rate_limiter.check_rate_limit(key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for {key}. Retry after {retry_after}s.",
            headers={"Retry-After": str(int(retry_after))},
        )


# Backward compatibility alias for operational controls
verify_ops_control_access = require_operator



@router.get(
    "/overview",
    summary="Get high-level system operations and health overview",
)
def get_operations_overview(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Retrieve operational status across API, Database, Worker scheduler, and recent pipeline executions."""
    # 1. Database Connectivity & Ping
    db_status = "healthy"
    db_latency_ms = 0.0
    t_db_start = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        db_latency_ms = round((time.perf_counter() - t_db_start) * 1000.0, 2)
    except Exception as e:
        logger.error("Ops Database check failed: %s", str(e))
        db_status = "unavailable"
        db_latency_ms = round((time.perf_counter() - t_db_start) * 1000.0, 2)

    # 2. Topic Metrics
    total_topics = 0
    active_topics = 0
    recently_refreshed_topics = 0
    if db_status == "healthy":
        try:
            total_topics = db.query(Topic).count()
            active_topics = db.query(Topic).filter(Topic.trending_score > 0.0).count()
            twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
            recently_refreshed_topics = (
                db.query(Topic).filter(Topic.updated_at >= twenty_four_hours_ago).count()
            )
        except Exception as e:
            logger.warning("Error fetching topic counts for ops overview: %s", str(e))

    # 3. Worker Scheduler Status
    worker_status = scheduler.get_status()

    # 4. Pipeline Run Snapshot & Incident Timestamps
    pipeline_metrics = ops_metrics.get_pipeline_metrics()
    recent_pipeline_runs = pipeline_metrics.get("recent_runs", [])
    
    last_successful_pipeline = next(
        (r["timestamp"] for r in recent_pipeline_runs if r.get("status") == "success"),
        None,
    )
    last_successful_synthesis = next(
        (
            r["timestamp"]
            for r in recent_pipeline_runs
            if r.get("status") == "success"
            and any(k in r.get("stages_ms", {}) for k in ("llm_perspective_synthesis", "synthesis"))
        ),
        None,
    )

    # 5. Source Ingestion Freshness
    source_health = ops_metrics.get_source_health_summary()
    last_successful_ingestion = {
        src: data.get("last_success") for src, data in source_health.items()
    }

    # 6. Active Alerts Summary
    alerts_summary = alert_manager.get_alerts_summary()

    # 7. Circuit Breakers Snapshot
    from app.core.resilience import circuit_registry
    breakers_status = circuit_registry.get_all_status()
    open_breakers = [name for name, s in breakers_status.items() if s.get("state") == "OPEN"]
    half_open_breakers = [name for name, s in breakers_status.items() if s.get("state") == "HALF_OPEN"]
    backend_info = resource_governor.coordinator.get_backend_info()

    # Overall System Health
    overall_health = "healthy"
    if db_status != "healthy":
        overall_health = "unavailable"
    elif alerts_summary.get("active_count", 0) > 0:
        has_critical = any(a.get("severity") == "critical" for a in alerts_summary.get("active_alerts", []))
        overall_health = "unavailable" if has_critical else "degraded"
    elif open_breakers or half_open_breakers or backend_info.get("fallback_active"):
        overall_health = "degraded"
    elif not worker_status["is_running"] and worker_status.get("last_error"):
        overall_health = "degraded"

    return {
        "status": overall_health,
        "timestamp": datetime.utcnow().isoformat(),
        "api": {
            "status": "healthy",
            "version": "2.0.0",
            "environment": os.getenv("ENVIRONMENT", "production"),
        },
        "database": {
            "status": db_status,
            "latency_ms": db_latency_ms,
            "total_topics": total_topics,
            "active_topics": active_topics,
            "recently_refreshed_24h": recently_refreshed_topics,
        },
        "governance": {
            "backend": backend_info.get("backend"),
            "fallback_active": backend_info.get("fallback_active"),
            "circuit_state": backend_info.get("circuit_state", "CLOSED"),
        },
        "resilience": {
            "all_healthy": len(open_breakers) == 0 and len(half_open_breakers) == 0,
            "open_circuits": open_breakers,
            "half_open_circuits": half_open_breakers,
            "total_breakers": len(breakers_status),
        },
        "worker": {
            "is_running": worker_status["is_running"],
            "total_runs": worker_status["total_runs"],
            "last_run_time": worker_status["last_run_time"],
            "next_scheduled_run": worker_status["next_scheduled_run"],
            "interval_hours": worker_status["worker_interval_hours"],
        },
        "incident_readiness": {
            "readiness_state": "ready" if db_status == "healthy" else "unready",
            "current_worker_cycle": worker_status["total_runs"],
            "last_database_check": {
                "status": db_status,
                "latency_ms": db_latency_ms,
                "timestamp": datetime.utcnow().isoformat(),
            },
            "last_successful_ingestion": last_successful_ingestion,
            "last_successful_pipeline": last_successful_pipeline,
            "last_successful_synthesis": last_successful_synthesis,
        },
        "alerts_summary": {
            "active_count": alerts_summary.get("active_count", 0),
            "resolved_count": alerts_summary.get("resolved_count", 0),
            "last_evaluation_time": alerts_summary.get("last_evaluation_time"),
        },
        "slo_summary": slo_manager.get_summary(),
        "recent_pipeline_runs": recent_pipeline_runs[:5],
        "pipeline_summary": {
            "total_runs": pipeline_metrics["total_runs"],
            "success_rate": pipeline_metrics["success_rate"],
            "avg_duration_ms": pipeline_metrics["avg_duration_ms"],
        },
    }


@router.get(
    "/alerts",
    summary="Get active and recently resolved production alerts",
)
def get_alerts(
    auto_evaluate: bool = Query(
        default=False,
        description="Optionally trigger an evaluation cycle before returning",
    ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Return active and recent resolved alerts without exposing credentials."""
    if auto_evaluate:
        return alert_manager.evaluate(db=db)
    return alert_manager.get_alerts_summary(db=db)


@router.get(
    "/pipeline-metrics",
    summary="Get detailed multi-stage pipeline latency and reliability metrics",
)
def get_pipeline_metrics(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Return aggregated telemetry across all pipeline stages, average timings, and slowest stages."""
    return ops_metrics.get_pipeline_metrics(db=db)


@router.get(
    "/source-health",
    summary="Get operational health and reliability of ingestion sources and LLM providers",
)
def get_source_health(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Return health metrics for Google News, Reddit, X, and OpenAI without exposing credentials."""
    return ops_metrics.get_source_health_summary(db=db)


@router.get(
    "/worker-metrics",
    summary="Get background worker execution cycles and scheduler metrics",
)
def get_worker_metrics(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Return scheduler state, cycles executed, and last refresh breakdown."""
    from app.database.models import WorkerCycle

    status_data = scheduler.get_status()
    last_res = status_data.get("last_run_result") or {}

    total_cycles = status_data["total_runs"]
    if total_cycles == 0 and db is not None:
        try:
            db_cycle_count = db.query(WorkerCycle).count()
            if db_cycle_count > 0:
                total_cycles = db_cycle_count
                latest_cycle = db.query(WorkerCycle).order_by(WorkerCycle.started_at.desc()).first()
                if latest_cycle and not status_data["last_run_time"]:
                    status_data["last_run_time"] = latest_cycle.started_at.isoformat()
                    last_res = {
                        "topics_considered": latest_cycle.topics_considered,
                        "topics_refreshed": latest_cycle.topics_refreshed,
                        "topics_skipped": latest_cycle.topics_skipped,
                        "topics_failed": latest_cycle.topics_failed,
                        "candidates_discovered": 0,
                    }
        except Exception as e:
            logger.warning("Error fetching persisted worker cycles: %s", e)

    return {
        "scheduler_running": status_data["is_running"],
        "cycle_count": total_cycles,
        "interval_hours": status_data["worker_interval_hours"],
        "last_cycle_time": status_data["last_run_time"],
        "next_scheduled_cycle": status_data["next_scheduled_run"],
        "last_error": status_data["last_error"],
        "last_cycle_summary": {
            "topics_considered": last_res.get("topics_considered", 0),
            "topics_refreshed": last_res.get("topics_refreshed", 0),
            "topics_skipped": last_res.get("topics_skipped", 0),
            "topics_failed": last_res.get("topics_failed", 0),
            "candidates_discovered": last_res.get("candidates_discovered", 0),
        },
    }


@router.get(
    "/history",
    summary="Query historical operational audit logs with filtering and pagination",
)
def get_ops_history(
    type: str = Query(
        default="all",
        description="Filter record type: all, pipeline_runs, source_executions, worker_cycles, alerts",
    ),
    component: Optional[str] = Query(
        default=None,
        description="Filter by component name or source identifier (e.g. google_news, reddit, worker, pipeline)",
    ),
    status: Optional[str] = Query(
        default=None,
        description="Filter by execution/alert status (e.g. success, failed, timeout, active, resolved)",
    ),
    topic_slug: Optional[str] = Query(
        default=None,
        description="Filter by topic slug (applicable to pipeline runs)",
    ),
    start_time: Optional[str] = Query(
        default=None,
        description="ISO datetime lower bound filter",
    ),
    end_time: Optional[str] = Query(
        default=None,
        description="ISO datetime upper bound filter",
    ),
    page: int = Query(default=1, ge=1, description="1-based page number"),
    limit: int = Query(default=50, ge=1, le=100, description="Items per page (max 100)"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve persisted operational history with structured audit metadata, strict transaction safety, and no raw data leakage."""
    from app.database.models import OperationalAlert, PipelineRun, SourceExecution, WorkerCycle

    start_dt = None
    end_dt = None
    if start_time:
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Invalid start_time format. Expected ISO 8601 string.",
            )
    if end_time:
        try:
            end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Invalid end_time format. Expected ISO 8601 string.",
            )

    items: List[Dict[str, Any]] = []
    total_count = 0

    valid_types = {"all", "pipeline_runs", "source_executions", "worker_cycles", "alerts"}
    if type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type '{type}'. Expected one of: {', '.join(sorted(valid_types))}",
        )

    # Helper filters
    def apply_time_filter(query, col):
        if start_dt:
            query = query.filter(col >= start_dt)
        if end_dt:
            query = query.filter(col <= end_dt)
        return query

    offset = (page - 1) * limit

    if type == "pipeline_runs":
        q = db.query(PipelineRun)
        if status:
            q = q.filter(PipelineRun.status == status)
        if topic_slug:
            q = q.filter(PipelineRun.topic_slug == topic_slug)
        if component:
            q = q.filter(PipelineRun.pipeline_type.ilike(f"%{component}%"))
        q = apply_time_filter(q, PipelineRun.started_at)
        total_count = q.count()
        rows = q.order_by(PipelineRun.started_at.desc()).offset(offset).limit(limit).all()
        for r in rows:
            d = r.to_dict()
            d["record_type"] = "pipeline_run"
            items.append(d)

    elif type == "source_executions":
        q = db.query(SourceExecution)
        if status:
            q = q.filter(SourceExecution.status == status)
        if component:
            q = q.filter(SourceExecution.source == component)
        q = apply_time_filter(q, SourceExecution.started_at)
        total_count = q.count()
        rows = q.order_by(SourceExecution.started_at.desc()).offset(offset).limit(limit).all()
        for r in rows:
            d = r.to_dict()
            d["record_type"] = "source_execution"
            items.append(d)

    elif type == "worker_cycles":
        q = db.query(WorkerCycle)
        if status:
            q = q.filter(WorkerCycle.status == status)
        q = apply_time_filter(q, WorkerCycle.started_at)
        total_count = q.count()
        rows = q.order_by(WorkerCycle.started_at.desc()).offset(offset).limit(limit).all()
        for r in rows:
            d = r.to_dict()
            d["record_type"] = "worker_cycle"
            items.append(d)

    elif type == "alerts":
        q = db.query(OperationalAlert)
        if status:
            q = q.filter(OperationalAlert.status == status)
        if component:
            q = q.filter(
                (OperationalAlert.component.ilike(f"%{component}%"))
                | (OperationalAlert.alert_type.ilike(f"%{component}%"))
            )
        q = apply_time_filter(q, OperationalAlert.last_seen)
        total_count = q.count()
        rows = q.order_by(OperationalAlert.last_seen.desc()).offset(offset).limit(limit).all()
        for r in rows:
            d = r.to_dict()
            d["record_type"] = "alert"
            items.append(d)

    else:
        # type == "all": aggregate across all operational tables
        p_q = apply_time_filter(db.query(PipelineRun), PipelineRun.started_at)
        if status:
            p_q = p_q.filter(PipelineRun.status == status)
        if topic_slug:
            p_q = p_q.filter(PipelineRun.topic_slug == topic_slug)
        if component:
            p_q = p_q.filter(PipelineRun.pipeline_type.ilike(f"%{component}%"))

        s_q = apply_time_filter(db.query(SourceExecution), SourceExecution.started_at)
        if status:
            s_q = s_q.filter(SourceExecution.status == status)
        if component:
            s_q = s_q.filter(SourceExecution.source == component)

        w_q = apply_time_filter(db.query(WorkerCycle), WorkerCycle.started_at)
        if status:
            w_q = w_q.filter(WorkerCycle.status == status)

        a_q = apply_time_filter(db.query(OperationalAlert), OperationalAlert.last_seen)
        if status:
            a_q = a_q.filter(OperationalAlert.status == status)
        if component:
            a_q = a_q.filter(
                (OperationalAlert.component.ilike(f"%{component}%"))
                | (OperationalAlert.alert_type.ilike(f"%{component}%"))
            )

        p_count = p_q.count()
        s_count = s_q.count()
        w_count = w_q.count()
        a_count = a_q.count()
        total_count = p_count + s_count + w_count + a_count

        # Fetch recent records from each table up to limit and merge
        fetch_limit = min(offset + limit, 500)
        p_rows = [
            dict(r.to_dict(), record_type="pipeline_run", timestamp=r.started_at)
            for r in p_q.order_by(PipelineRun.started_at.desc()).limit(fetch_limit).all()
        ]
        s_rows = [
            dict(r.to_dict(), record_type="source_execution", timestamp=r.started_at)
            for r in s_q.order_by(SourceExecution.started_at.desc()).limit(fetch_limit).all()
        ]
        w_rows = [
            dict(r.to_dict(), record_type="worker_cycle", timestamp=r.started_at)
            for r in w_q.order_by(WorkerCycle.started_at.desc()).limit(fetch_limit).all()
        ]
        a_rows = [
            dict(r.to_dict(), record_type="alert", timestamp=r.last_seen)
            for r in a_q.order_by(OperationalAlert.last_seen.desc()).limit(fetch_limit).all()
        ]

        all_records = p_rows + s_rows + w_rows + a_rows
        # Sort descending by timestamp
        all_records.sort(
            key=lambda x: x.get("timestamp") or datetime.min,
            reverse=True,
        )

        for rec in all_records[offset : offset + limit]:
            if "timestamp" in rec and isinstance(rec["timestamp"], datetime):
                rec["timestamp"] = rec["timestamp"].isoformat()
            items.append(rec)

    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1

    return {
        "status": "success",
        "type": type,
        "page": page,
        "limit": limit,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
        "items": items,
    }


@router.get(
    "/audit-logs",
    summary="Query persisted security audit trail with filtering and pagination",
    dependencies=[Depends(require_operator)],
)
def get_security_audit_logs(
    action: Optional[str] = Query(default=None, description="Filter by action name (e.g. auth_failure, backup_created)"),
    status: Optional[str] = Query(default=None, description="Filter by status (e.g. allowed, denied, failed)"),
    actor: Optional[str] = Query(default=None, description="Filter by actor identifier"),
    start_time: Optional[str] = Query(default=None, description="ISO datetime lower bound"),
    end_time: Optional[str] = Query(default=None, description="ISO datetime upper bound"),
    page: int = Query(default=1, ge=1, description="1-based page number"),
    limit: int = Query(default=50, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve security audit events, authorization logs, and administrative actions without leaking credentials."""
    q = db.query(SecurityAuditLog)
    if action:
        q = q.filter(SecurityAuditLog.action == action)
    if status:
        q = q.filter(SecurityAuditLog.status == status)
    if actor:
        q = q.filter(SecurityAuditLog.actor == actor)

    if start_time:
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            q = q.filter(SecurityAuditLog.timestamp >= start_dt)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid start_time format. Expected ISO 8601.")

    if end_time:
        try:
            end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            q = q.filter(SecurityAuditLog.timestamp <= end_dt)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid end_time format. Expected ISO 8601.")

    total_count = q.count()
    offset = (page - 1) * limit
    rows = q.order_by(SecurityAuditLog.timestamp.desc()).offset(offset).limit(limit).all()
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1

    return {
        "status": "success",
        "page": page,
        "limit": limit,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
        "audit_logs": [r.to_dict() for r in rows],
    }


# ==========================================
# Protected Operational Controls
# ==========================================


@router.post(
    "/alerts/evaluate",
    summary="Trigger immediate alert evaluation cycle",
    dependencies=[Depends(require_operator)],
)
def trigger_ops_alert_evaluate(request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Evaluate all alert rules against live telemetry, database, and worker states."""
    try:
        res = alert_manager.evaluate(db=db)
        record_audit_event(
            action="alert_evaluation_triggered",
            actor=getattr(request.state, "actor", "operator"),
            role=getattr(request.state, "role", SecurityRole.OPERATOR).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "operator")),
            resource="/api/ops/alerts/evaluate",
            ip_address=request.client.host if request.client else None,
            status="allowed",
            db=db,
        )
        return res
    except Exception as e:
        logger.error("Alert evaluation failed: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Alert evaluation failed: {str(e)[:100]}",
        )


@router.post(
    "/run-trending",
    summary="Trigger immediate trending discovery cycle",
    dependencies=[Depends(require_operator)],
)
def trigger_ops_trending_discovery(
    request: Request,
    limit_per_provider: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Trigger external trend signal discovery across providers."""
    _enforce_ops_rate_limit("ops:trending")
    try:
        res = run_candidate_discovery_job(limit_per_provider=limit_per_provider)
        record_audit_event(
            action="trending_discovery_triggered",
            actor=getattr(request.state, "actor", "operator"),
            role=getattr(request.state, "role", SecurityRole.OPERATOR).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "operator")),
            resource="/api/ops/run-trending",
            ip_address=request.client.host if request.client else None,
            status="allowed",
            details={"limit_per_provider": limit_per_provider},
            db=db,
        )
        return res
    except Exception as e:
        logger.error("Ops run-trending failed: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Trending discovery execution failed: {str(e)[:100]}",
        )


@router.post(
    "/refresh-topic/{slug}",
    summary="Trigger operational refresh for a specific topic",
    dependencies=[Depends(require_operator)],
)
def trigger_ops_refresh_topic(
    slug: str,
    request: Request,
    force: bool = Query(default=True, description="Force refresh even if topic appears stagnant"),
    min_volume_threshold: int = Query(default=30, ge=2),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Trigger worker evaluation and refresh cycle for a single topic."""
    if not validate_slug(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid slug format",
        )

    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    worker = TopicRefreshWorker()
    try:
        result = worker.refresh_topic(
            topic=topic,
            db=db,
            min_volume_threshold=min_volume_threshold,
            force_refresh=force,
        )
        record_audit_event(
            action="topic_refresh_triggered",
            actor=getattr(request.state, "actor", "operator"),
            role=getattr(request.state, "role", SecurityRole.OPERATOR).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "operator")),
            resource=f"/api/ops/refresh-topic/{slug}",
            ip_address=request.client.host if request.client else None,
            status="allowed",
            details={"slug": slug, "force": force},
            db=db,
        )
        return {
            "status": "success",
            "topic_slug": slug,
            "topic_id": topic.id,
            "refresh_result": result,
            "new_trending_score": topic.trending_score,
        }
    except Exception as e:
        logger.error("Ops refresh topic '%s' failed: %s", slug, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Topic refresh failed: {str(e)[:100]}",
        )


@router.post(
    "/reprocess-topic/{slug}",
    summary="Trigger full pipeline reprocessing for a topic (Ingest -> Merge -> Cluster -> Synthesize)",
    dependencies=[Depends(require_admin)],
)
def trigger_ops_reprocess_topic(
    slug: str,
    request: Request,
    limit_per_source: int = Query(default=50, ge=1, le=100),
    min_volume_threshold: int = Query(default=30, ge=2),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:

    """Execute complete end-to-end pipeline reprocessing with structured telemetry tracking."""
    _enforce_ops_rate_limit("ops:reprocess_topic")
    if not validate_slug(slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid slug format",
        )

    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    tracker = PipelineTimingTracker("ops_reprocess_topic", topic_slug=slug)

    try:
        # 1. Ingestion
        with tracker.track("ingestion_sources_fanout"):
            ingestion_pipeline = IngestionPipeline()
            ingestion_res = ingestion_pipeline.run(
                topic=topic, db=db, limit_per_source=limit_per_source
            )

        # 2. Clustering
        with tracker.track("hdbscan_clustering"):
            cluster_pipeline = ClusterPipeline()
            cluster_res = cluster_pipeline.run_for_topic(
                topic=topic,
                db=db,
                min_volume_threshold=min_volume_threshold,
            )

        # 3. Perspective Synthesis
        synthesis_res = None
        if cluster_res.get("status") == "success":
            with tracker.track("llm_perspective_synthesis"):
                perspective_pipeline = PerspectivePipeline()
                synthesis_res = perspective_pipeline.run_synthesis_for_topic(
                    topic=topic,
                    db=db,
                    min_volume_threshold=min_volume_threshold,
                    cluster_data=cluster_res,
                )

        # 4. Score recalculation
        with tracker.track("trending_score_recalculation"):
            scorer = TrendingScorer()
            score_breakdown = scorer.calculate_topic_score(topic=topic, db=db)
            topic.trending_score = score_breakdown.final_score
            db.commit()
            db.refresh(topic)

        summary = tracker.finish(status="success")

        return {
            "status": "success",
            "topic": {
                "id": topic.id,
                "slug": topic.slug,
                "title": topic.title,
                "trending_score": topic.trending_score,
                "source_coverage": topic.source_coverage,
            },
            "ingestion": ingestion_res,
            "clustering": cluster_res,
            "synthesis": synthesis_res,
            "timings": summary,
        }
    except Exception as e:
        logger.error("Ops reprocess topic '%s' failed: %s", slug, str(e))
        tracker.finish(status="failed", error=str(e)[:100])
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reprocess failed: {str(e)[:100]}",
        )


# ==========================================
# Database Backup & Disaster Recovery APIs
# ==========================================


@router.get(
    "/backups",
    summary="Get recent database backup records, status, and verification state",
)
def get_backups(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Return recent backup metadata without exposing database passwords or filesystem secrets."""
    from app.database.backup import BackupConfig, is_backup_running, list_backups

    backups = list_backups(db=db, limit=limit)
    cfg = BackupConfig()

    last_success = next((b for b in backups if b.get("status") in ("success", "verified")), None)
    last_failed = next((b for b in backups if b.get("status") in ("failed", "corrupted")), None)

    return {
        "status": "success",
        "backup_enabled": cfg.enabled,
        "backup_running": is_backup_running(),
        "total_backups": len(backups),
        "last_successful_backup": last_success,
        "last_failed_backup": last_failed,
        "retention_policy": {
            "retention_count": cfg.retention_count,
            "retention_days": cfg.retention_days,
            "interval_hours": cfg.interval_hours,
            "compression": cfg.compression,
            "verify_after_create": cfg.verify_after_create,
        },
        "backups": backups,
    }


@router.post(
    "/backups/create",
    summary="Trigger immediate logical database backup",
    dependencies=[Depends(require_admin)],
)
def trigger_ops_create_backup(
    request: Request,
    dry_run: bool = Query(default=False, description="Simulate backup creation without writing to disk"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Execute logical database backup with SHA-256 checksum and metadata persistence."""
    from app.database.backup import BackupConfig, create_backup

    _enforce_ops_rate_limit("ops:backup_create")
    try:
        cfg = BackupConfig()
        result = create_backup(db=db, config=cfg, dry_run=dry_run)
        record_audit_event(
            action="database_backup_created",
            actor=getattr(request.state, "actor", "admin"),
            role=getattr(request.state, "role", SecurityRole.ADMIN).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "admin")),
            resource="/api/ops/backups/create",
            ip_address=request.client.host if request.client else None,
            status="allowed",
            details={"dry_run": dry_run, "backup_id": result.get("backup_id"), "status": result.get("status")},
            db=db,
        )
        return {
            "status": "success",
            "backup": result,
        }
    except Exception as e:
        logger.error("Ops create backup failed: %s", str(e))
        record_audit_event(
            action="database_backup_failed",
            actor=getattr(request.state, "actor", "admin"),
            role=getattr(request.state, "role", SecurityRole.ADMIN).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "admin")),
            resource="/api/ops/backups/create",
            ip_address=request.client.host if request.client else None,
            status="error",
            error_message=str(e),
            db=db,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Backup creation failed: {str(e)[:120]}",
        )


@router.post(
    "/backups/verify/{backup_id}",
    summary="Verify integrity of a specific database backup",
    dependencies=[Depends(require_admin)],
)
def trigger_ops_verify_backup(
    backup_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Verify backup file existence, non-emptiness, and SHA-256 checksum match."""
    from app.database.backup import verify_backup

    try:
        result = verify_backup(backup_id=backup_id, db=db)
        record_audit_event(
            action="database_backup_verified",
            actor=getattr(request.state, "actor", "admin"),
            role=getattr(request.state, "role", SecurityRole.ADMIN).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "admin")),
            resource=f"/api/ops/backups/verify/{backup_id}",
            ip_address=request.client.host if request.client else None,
            status="allowed",
            details={"backup_id": backup_id, "is_valid": result.get("is_valid")},
            db=db,
        )
        return {
            "status": "success",
            "verification": result,
        }
    except Exception as e:
        logger.error("Ops verify backup failed: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Backup verification failed: {str(e)[:120]}",
        )


# ==========================================
# Resource Governance Endpoints
# ==========================================


@router.get(
    "/resource-usage",
    summary="Get current resource utilization, rate-limit usage, and budget status",
    dependencies=[Depends(require_operator)],
)
def get_resource_usage() -> Dict[str, Any]:
    """Return comprehensive resource governance metrics without exposing secrets."""
    rate_limit_usage = resource_governor.rate_limiter.get_usage()
    concurrency_usage = resource_governor.concurrency_governor.get_usage()
    cost_usage = resource_governor.cost_tracker.get_usage()
    external_usage = resource_governor.external_governor.get_usage()
    synthesis_usage = resource_governor.synthesis_tracker.get_usage()
    budgets = resource_governor.budget_manager.get_all_budgets()
    thresholds = resource_governor.utilization_monitor.get_thresholds()

    # Compute budget utilization percentages with warning status
    budget_utilization = {}
    for resource, limit in budgets.items():
        current = 0
        if resource == "max_concurrent_pipelines":
            current = concurrency_usage.get("pipeline", {}).get("current", 0)
        elif resource == "max_concurrent_source_calls":
            current = concurrency_usage.get("source_call", {}).get("current", 0)
        elif resource == "max_backup_operations":
            current = concurrency_usage.get("backup", {}).get("current", 0)
        elif resource == "max_maintenance_operations":
            current = concurrency_usage.get("maintenance", {}).get("current", 0)

        pct = round((current / limit) * 100, 1) if limit > 0 else 0.0
        status_val = resource_governor.utilization_monitor.get_status(current, limit)
        budget_utilization[resource] = {
            "current": current,
            "limit": limit,
            "utilization_pct": pct,
            "status": status_val,
        }

    backend_info = resource_governor.coordinator.get_backend_info()

    return {
        "governance_backend": backend_info,
        "rate_limits": rate_limit_usage,
        "concurrency": concurrency_usage,
        "cost_tracking": cost_usage,
        "external_requests": external_usage,
        "synthesis_rate": synthesis_usage,
        "budget_utilization": budget_utilization,
        "thresholds": thresholds,
    }


@router.get(
    "/resource-budgets",
    summary="Get active configured resource budget limits",
    dependencies=[Depends(require_operator)],
)
def get_resource_budgets() -> Dict[str, Any]:
    """Return all configured resource budget limits without exposing secrets."""
    return {
        "governance_backend": resource_governor.coordinator.get_backend_info(),
        "budgets": resource_governor.budget_manager.get_all_budgets(),
        "thresholds": resource_governor.utilization_monitor.get_thresholds(),
        "external_source_governance": {
            source: {
                "max_concurrent": data.get("max_concurrent"),
                "timeout_seconds": data.get("timeout_seconds"),
                "max_retries": data.get("max_retries"),
                "hourly_budget": data.get("hourly_budget"),
            }
            for source, data in resource_governor.external_governor.get_usage().items()
        },
    }


@router.get(
    "/resilience",
    summary="Get platform circuit breaker and resilience status",
)
def get_resilience_status() -> Dict[str, Any]:
    """Return status and telemetry of all platform circuit breakers and degraded states."""
    from app.core.resilience import circuit_registry
    breakers = circuit_registry.get_all_status()
    all_healthy = circuit_registry.is_all_healthy()
    backend_info = resource_governor.coordinator.get_backend_info()

    return {
        "status": "healthy" if (all_healthy and not backend_info.get("fallback_active")) else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "all_healthy": all_healthy,
        "circuit_breakers": breakers,
        "governance_backend": backend_info,
    }


@router.post(
    "/resilience/reset",
    summary="Reset platform circuit breakers and governance failover",
    dependencies=[Depends(require_admin)],
)
def reset_resilience_state(
    request: Request,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Reset all circuit breakers to CLOSED and attempt to reconnect shared governance."""
    from app.core.resilience import circuit_registry
    circuit_registry.reset_all()
    recovered = resource_governor.coordinator.reset_fallback()
    record_audit_event(
        action="resilience_circuit_reset",
        actor=getattr(request.state, "actor", "admin"),
        role=getattr(request.state, "role", SecurityRole.ADMIN).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "admin")),
        resource="/api/ops/resilience/reset",
        ip_address=request.client.host if request.client else None,
        status="allowed",
        details={"shared_governance_reconnected": recovered},
        db=db,
    )
    return {
        "status": "success",
        "message": "Circuit breakers reset to CLOSED",
        "shared_governance_reconnected": recovered,
        "backend_info": resource_governor.coordinator.get_backend_info(),
    }



@router.get(
    "/slos",
    summary="Get Service Level Objectives (SLO) compliance and error budgets",
)
def get_slo_status() -> Dict[str, Any]:
    """Return all platform SLO evaluations, remaining error budgets, and burn rates."""
    return slo_manager.get_summary()


@router.get(
    "/metrics",
    summary="Get structured JSON snapshot of all platform counters, gauges, and histograms",
)
def get_platform_metrics() -> Dict[str, Any]:
    """Return point-in-time snapshot of all registered platform metrics and percentiles."""
    return platform_metrics.get_all_metrics()



