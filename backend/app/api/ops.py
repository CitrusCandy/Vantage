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
    return alert_manager.get_alerts_summary()


@router.get(
    "/pipeline-metrics",
    summary="Get detailed multi-stage pipeline latency and reliability metrics",
)
def get_pipeline_metrics() -> Dict[str, Any]:
    """Return aggregated telemetry across all pipeline stages, average timings, and slowest stages."""
    return ops_metrics.get_pipeline_metrics()


@router.get(
    "/source-health",
    summary="Get operational health and reliability of ingestion sources and LLM providers",
)
def get_source_health() -> Dict[str, Any]:
    """Return health metrics for Google News, Reddit, X, and OpenAI without exposing credentials."""
    return ops_metrics.get_source_health_summary()


@router.get(
    "/worker-metrics",
    summary="Get background worker execution cycles and scheduler metrics",
)
def get_worker_metrics() -> Dict[str, Any]:
    """Return scheduler state, cycles executed, and last refresh breakdown."""
    status_data = scheduler.get_status()
    last_res = status_data.get("last_run_result") or {}
    return {
        "scheduler_running": status_data["is_running"],
        "cycle_count": status_data["total_runs"],
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
