from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core import resource_governor
from app.database.database import get_db
from app.workers.jobs import run_candidate_discovery_job, run_trending_refresh_job
from app.workers.scheduler import scheduler

router = APIRouter(prefix="/workers", tags=["Workers"])


@router.get(
    "/status",
    summary="Get background worker and scheduler status",
)
def get_worker_status() -> Dict[str, Any]:
    """Retrieve operational status, last execution results, and cadence of the scheduler."""
    return scheduler.get_status()


@router.post(
    "/start",
    summary="Start background scheduler",
)
def start_worker_scheduler(
    auto_discover: bool = Query(default=False, description="Automatically discover and register candidate topics"),
) -> Dict[str, Any]:
    """Start background scheduler loop if not running."""
    scheduler.start(auto_discover=auto_discover)
    return {"message": "Background scheduler started", "status": scheduler.get_status()}


@router.post(
    "/stop",
    summary="Stop background scheduler",
)
def stop_worker_scheduler() -> Dict[str, Any]:
    """Stop the background scheduler thread."""
    scheduler.stop()
    return {"message": "Background scheduler stopped", "status": scheduler.get_status()}


@router.post(
    "/refresh-trending",
    summary="Trigger trending score calculation and topic refresh cycle",
)
def trigger_trending_refresh(
    auto_discover: bool = Query(default=False, description="Discover candidate topics before refresh"),
    force_refresh_all: bool = Query(default=False, description="Force refresh even for stagnant topics"),
    min_volume_threshold: int = Query(default=30, ge=2, description="Minimum discourse volume for ML pipeline"),
) -> Dict[str, Any]:
    """Synchronously execute a full trending evaluation and pipeline refresh cycle."""
    allowed, retry_after = resource_governor.rate_limiter.check_rate_limit("ops:trending")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry_after}s.",
            headers={"Retry-After": str(int(retry_after))},
        )
    return run_trending_refresh_job(
        auto_discover=auto_discover,
        min_volume_threshold=min_volume_threshold,
        force_refresh_all=force_refresh_all,
    )


@router.post(
    "/discover-trends",
    summary="Discover candidate trending topics across providers",
)
def trigger_candidate_discovery(
    limit_per_provider: int = Query(default=10, ge=1, le=50),
) -> Dict[str, Any]:
    """Query external trend signal providers and return deduplicated candidate topics."""
    return run_candidate_discovery_job(limit_per_provider=limit_per_provider)
