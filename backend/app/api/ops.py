from datetime import datetime, timedelta
import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.alerting import alert_manager
from app.core.security import validate_slug
from app.core.telemetry import PipelineTimingTracker, ops_metrics
from app.database.database import get_db
from app.database.models import Topic
from app.ingestion.pipeline import IngestionPipeline
from app.llm.pipeline import PerspectivePipeline
from app.processing.cluster_pipeline import ClusterPipeline
from app.workers.jobs import run_candidate_discovery_job, run_trending_refresh_job
from app.workers.scheduler import scheduler
from app.workers.topic_refresh import TopicRefreshWorker
from app.workers.trending import TrendingScorer

logger = logging.getLogger("app.api.ops")

router = APIRouter(prefix="/ops", tags=["Operations & Observability"])


def verify_ops_control_access(
    x_ops_key: Optional[str] = Header(default=None, alias="X-Ops-Key"),
) -> None:
    """Configurable security guard for operational modification endpoints."""
    ops_enabled = os.getenv("ENABLE_OPS_CONTROLS", "true").lower() in ("true", "1", "yes")
    if not ops_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operational control endpoints are disabled in this environment",
        )

    required_key = os.getenv("OPS_API_KEY", "").strip()
    if required_key and x_ops_key != required_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Ops-Key header for operational controls",
        )


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

    # Overall System Health
    overall_health = "healthy"
    if db_status != "healthy":
        overall_health = "unavailable"
    elif alerts_summary.get("active_count", 0) > 0:
        has_critical = any(a.get("severity") == "critical" for a in alerts_summary.get("active_alerts", []))
        overall_health = "unavailable" if has_critical else "degraded"
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


# ==========================================
# Protected Operational Controls
# ==========================================


@router.post(
    "/alerts/evaluate",
    summary="Trigger immediate alert evaluation cycle",
    dependencies=[Depends(verify_ops_control_access)],
)
def trigger_ops_alert_evaluate(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Evaluate all alert rules against live telemetry, database, and worker states."""
    try:
        return alert_manager.evaluate(db=db)
    except Exception as e:
        logger.error("Alert evaluation failed: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Alert evaluation failed: {str(e)[:100]}",
        )


@router.post(
    "/run-trending",
    summary="Trigger immediate trending discovery cycle",
    dependencies=[Depends(verify_ops_control_access)],
)
def trigger_ops_trending_discovery(
    limit_per_provider: int = Query(default=10, ge=1, le=50),
) -> Dict[str, Any]:
    """Trigger external trend signal discovery across providers."""
    try:
        return run_candidate_discovery_job(limit_per_provider=limit_per_provider)
    except Exception as e:
        logger.error("Ops run-trending failed: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Trending discovery execution failed: {str(e)[:100]}",
        )


@router.post(
    "/refresh-topic/{slug}",
    summary="Trigger operational refresh for a specific topic",
    dependencies=[Depends(verify_ops_control_access)],
)
def trigger_ops_refresh_topic(
    slug: str,
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
    dependencies=[Depends(verify_ops_control_access)],
)
def trigger_ops_reprocess_topic(
    slug: str,
    limit_per_source: int = Query(default=50, ge=1, le=100),
    min_volume_threshold: int = Query(default=30, ge=2),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Execute complete end-to-end pipeline reprocessing with structured telemetry tracking."""
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
    dependencies=[Depends(verify_ops_control_access)],
)
def trigger_ops_create_backup(
    dry_run: bool = Query(default=False, description="Simulate backup creation without writing to disk"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Execute logical database backup with SHA-256 checksum and metadata persistence."""
    from app.database.backup import BackupConfig, create_backup

    try:
        cfg = BackupConfig()
        result = create_backup(db=db, config=cfg, dry_run=dry_run)
        return {
            "status": "success",
            "backup": result,
        }
    except Exception as e:
        logger.error("Ops create backup failed: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Backup creation failed: {str(e)[:120]}",
        )


@router.post(
    "/backups/verify/{backup_id}",
    summary="Verify integrity of a specific database backup",
    dependencies=[Depends(verify_ops_control_access)],
)
def trigger_ops_verify_backup(
    backup_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Verify backup file existence, non-emptiness, and SHA-256 checksum match."""
    from app.database.backup import verify_backup

    try:
        result = verify_backup(backup_id=backup_id, db=db)
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

