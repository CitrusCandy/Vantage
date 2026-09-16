import logging
from typing import Any, Dict, Optional

from app.database.database import SessionLocal
from app.workers.discovery import CandidateTopic, TrendDiscoveryService
from app.workers.topic_refresh import RefreshSummary, TopicRefreshWorker

logger = logging.getLogger("app.workers.jobs")


def run_trending_refresh_job(
    auto_discover: bool = False,
    min_volume_threshold: int = 30,
    force_refresh_all: bool = False,
) -> Dict[str, Any]:
    """Execute the full trending refresh job using a standalone database session."""
    db = SessionLocal()
    try:
        worker = TopicRefreshWorker()
        summary: RefreshSummary = worker.run_refresh_cycle(
            db=db,
            auto_discover=auto_discover,
            min_volume_threshold=min_volume_threshold,
            force_refresh_all=force_refresh_all,
        )
        return {
            "status": "completed",
            "total_topics_evaluated": summary.total_topics_evaluated,
            "scores_updated": summary.scores_updated,
            "refreshed_topics": summary.refreshed_topics,
            "skipped_stagnant_topics": summary.skipped_stagnant_topics,
            "new_candidates_created": summary.new_candidates_created,
            "errors": summary.errors,
        }
    finally:
        db.close()


def run_candidate_discovery_job(limit_per_provider: int = 10) -> Dict[str, Any]:
    """Discover candidate trending topics across trend signal providers."""
    service = TrendDiscoveryService()
    candidates = service.discover_trending_topics(limit_per_provider=limit_per_provider)
    return {
        "status": "success",
        "candidate_count": len(candidates),
        "candidates": [c.to_dict() for c in candidates],
    }
