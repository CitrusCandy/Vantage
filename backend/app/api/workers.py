from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core import resource_governor
from app.core.security import SecurityRole, require_admin, require_operator
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
    dependencies=[Depends(require_admin)],
)
def start_worker_scheduler(
    request: Request,
    auto_discover: bool = Query(default=False, description="Automatically discover and register candidate topics"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Start background scheduler loop if not running."""
    scheduler.start(auto_discover=auto_discover)
    record_audit_event(
        action="worker_scheduler_started",
        actor=getattr(request.state, "actor", "admin"),
        role=getattr(request.state, "role", SecurityRole.ADMIN).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "admin")),
        resource="/api/workers/start",
        ip_address=request.client.host if request.client else None,
        status="allowed",
        details={"auto_discover": auto_discover},
        db=db,
    )
    return {"message": "Background scheduler started", "status": scheduler.get_status()}


@router.post(
    "/stop",
    summary="Stop background scheduler",
    dependencies=[Depends(require_admin)],
)
def stop_worker_scheduler(
    request: Request,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Stop the background scheduler thread."""
    scheduler.stop()
    record_audit_event(
        action="worker_scheduler_stopped",
        actor=getattr(request.state, "actor", "admin"),
        role=getattr(request.state, "role", SecurityRole.ADMIN).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "admin")),
        resource="/api/workers/stop",
        ip_address=request.client.host if request.client else None,
        status="allowed",
        db=db,
    )
    return {"message": "Background scheduler stopped", "status": scheduler.get_status()}


@router.post(
    "/refresh-trending",
    summary="Trigger trending score calculation and topic refresh cycle",
    dependencies=[Depends(require_operator)],
)
def trigger_trending_refresh(
    request: Request,
    auto_discover: bool = Query(default=False, description="Discover candidate topics before refresh"),
    force_refresh_all: bool = Query(default=False, description="Force refresh even for stagnant topics"),
    min_volume_threshold: int = Query(default=30, ge=2, description="Minimum discourse volume for ML pipeline"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Synchronously execute a full trending evaluation and pipeline refresh cycle."""
    allowed, retry_after = resource_governor.rate_limiter.check_rate_limit("ops:trending")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry_after}s.",
            headers={"Retry-After": str(int(retry_after))},
        )
    res = run_trending_refresh_job(
        auto_discover=auto_discover,
        min_volume_threshold=min_volume_threshold,
        force_refresh_all=force_refresh_all,
    )
    record_audit_event(
        action="worker_trending_refresh_triggered",
        actor=getattr(request.state, "actor", "operator"),
        role=getattr(request.state, "role", SecurityRole.OPERATOR).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "operator")),
        resource="/api/workers/refresh-trending",
        ip_address=request.client.host if request.client else None,
        status="allowed",
        details={"auto_discover": auto_discover, "force_refresh_all": force_refresh_all},
        db=db,
    )
    return res


@router.post(
    "/discover-trends",
    summary="Discover candidate trending topics across providers",
    dependencies=[Depends(require_operator)],
)
def trigger_candidate_discovery(
    request: Request,
    limit_per_provider: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Query external trend signal providers and return deduplicated candidate topics."""
    res = run_candidate_discovery_job(limit_per_provider=limit_per_provider)
    record_audit_event(
        action="worker_discover_trends_triggered",
        actor=getattr(request.state, "actor", "operator"),
        role=getattr(request.state, "role", SecurityRole.OPERATOR).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "operator")),
        resource="/api/workers/discover-trends",
        ip_address=request.client.host if request.client else None,
        status="allowed",
        details={"limit_per_provider": limit_per_provider},
        db=db,
    )
    return res
