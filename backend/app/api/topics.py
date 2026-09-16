from datetime import datetime
import logging
import re
from typing import Any, Dict, List, Optional
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core import resource_governor
from app.core.security import sanitize_search_query, validate_slug
from app.database.database import get_db
from app.database.models import Topic
from app.database.schemas import (
    TopicCreate,
    TopicDetailResponse,
    TopicResponse,
    TopicUpdate,
)
from app.ingestion.merge_pipeline import MergePipeline
from app.ingestion.pipeline import IngestionPipeline
from app.llm.pipeline import PerspectivePipeline
from app.processing.cluster_pipeline import ClusterPipeline

logger = logging.getLogger("app.api.topics")

router = APIRouter(prefix="/topics", tags=["Topics"])


def _enforce_rate_limit(key: str):
    """Check rate limit for the given key and raise HTTP 429 if exceeded."""
    allowed, retry_after = resource_governor.rate_limiter.check_rate_limit(key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for {key}. Retry after {retry_after}s.",
            headers={"Retry-After": str(int(retry_after))},
        )


def generate_slug(text: str) -> str:
    """Generate a clean, URL-safe slug from text."""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", normalized.lower()).strip()
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug or "topic"


def get_unique_slug(db: Session, base_slug: str, current_topic_id: Optional[int] = None) -> str:
    """Ensure generated slug is unique by appending numerical suffixes if necessary."""
    slug = base_slug
    counter = 1
    while True:
        query = db.query(Topic).filter(Topic.slug == slug)
        if current_topic_id is not None:
            query = query.filter(Topic.id != current_topic_id)
        if not query.first():
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1


@router.post(
    "",
    response_model=TopicResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new topic",
)
def create_topic(
    topic_in: TopicCreate,
    db: Session = Depends(get_db),
):
    _enforce_rate_limit("public:topic_create")
    """Create a new topic with initialized search/trending metrics and URL-safe slug."""
    raw_slug = topic_in.slug if topic_in.slug else generate_slug(topic_in.title)
    unique_slug = get_unique_slug(db, raw_slug)

    initial_coverage = topic_in.source_coverage or {
        "google_news": 0,
        "reddit": 0,
        "x": 0,
        "total_combined": 0,
    }

    topic = Topic(
        title=topic_in.title.strip(),
        slug=unique_slug,
        search_count=0,
        trending_score=0.0,
        source_coverage=initial_coverage,
        last_clustered_at=None,
        updated_at=datetime.utcnow(),
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


@router.get(
    "",
    response_model=List[TopicResponse],
    summary="List all topics",
)
def list_topics(
    search: Optional[str] = Query(
        default=None,
        description="Optional filter by topic title or slug",
    ),
    db: Session = Depends(get_db),
):
    """Retrieve topics ordered by updated_at descending with optional search filtering."""
    query = db.query(Topic)
    if search:
        clean_search = sanitize_search_query(search)
        if clean_search:
            search_term = f"%{clean_search}%"
            query = query.filter(
                or_(
                    Topic.title.ilike(search_term, escape="\\"),
                    Topic.slug.ilike(search_term, escape="\\"),
                )
            )
    return query.order_by(Topic.updated_at.desc()).limit(200).all()


@router.get(
    "/trending",
    response_model=List[TopicResponse],
    summary="Get top trending topics",
)
def get_trending_topics(
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of trending topics to return",
    ),
    min_score: float = Query(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum trending score threshold",
    ),
    db: Session = Depends(get_db),
):
    """Retrieve topics sorted by trending_score descending with optional minimum score filtering."""
    return (
        db.query(Topic)
        .filter(Topic.trending_score >= min_score)
        .order_by(Topic.trending_score.desc(), Topic.updated_at.desc())
        .limit(limit)
        .all()
    )


@router.get(
    "/{slug}",
    response_model=TopicDetailResponse,
    summary="Get a topic by slug",
)
def get_topic_by_slug(
    slug: str,
    db: Session = Depends(get_db),
):
    """Retrieve a single topic along with its perspectives by slug."""
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )
    return topic


@router.patch(
    "/{slug}",
    response_model=TopicResponse,
    summary="Update a topic",
)
def update_topic(
    slug: str,
    topic_in: TopicUpdate,
    db: Session = Depends(get_db),
):
    """Update mutable topic fields (title, source coverage). Analytical fields are protected."""
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    if topic_in.title is not None:
        topic.title = topic_in.title.strip()
    if topic_in.source_coverage is not None:
        topic.source_coverage = topic_in.source_coverage

    topic.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(topic)
    return topic


@router.delete(
    "/{slug}",
    status_code=status.HTTP_200_OK,
    summary="Delete a topic",
)
def delete_topic(
    slug: str,
    db: Session = Depends(get_db),
):
    """Safely delete a topic and its associated child records."""
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    db.delete(topic)
    db.commit()
    return {"message": f"Topic '{slug}' and its associated records have been deleted successfully"}


@router.post(
    "/{slug}/ingest",
    status_code=status.HTTP_200_OK,
    summary="Trigger multi-source ingestion into staging tables and merge",
)
def ingest_topic(
    slug: str,
    limit_per_source: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Trigger independent scrapers (Google News, Reddit, X) into staging tables followed by merge."""
    _enforce_rate_limit("public:ingestion")
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    clamped_limit = resource_governor.budget_manager.clamp("max_ingestion_items_per_source", limit_per_source)
    pipeline = IngestionPipeline()
    return pipeline.run(topic=topic, db=db, limit_per_source=clamped_limit)


@router.post(
    "/{slug}/merge",
    status_code=status.HTTP_200_OK,
    summary="Merge staging tables into combined_raw_data",
)
def merge_topic_staging(
    slug: str,
    db: Session = Depends(get_db),
):
    """Execute merge/normalization from raw_google_news, raw_reddit, raw_x into combined_raw_data."""
    _enforce_rate_limit("public:merge")
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    pipeline = MergePipeline()
    return pipeline.merge_topic_staging_data(topic=topic, db=db)


@router.post(
    "/{slug}/cluster",
    status_code=status.HTTP_200_OK,
    summary="Trigger clustering for a topic",
)
def cluster_topic(
    slug: str,
    min_volume_threshold: int = Query(
        default=30,
        ge=2,
        description="Minimum usable discourse posts required before clustering",
    ),
    db: Session = Depends(get_db),
):
    """Run preprocessing, embeddings generation, HDBSCAN clustering on combined dataset."""
    _enforce_rate_limit("public:clustering")
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    pipeline = ClusterPipeline()
    return pipeline.run_for_topic(
        topic=topic,
        db=db,
        min_volume_threshold=min_volume_threshold,
    )


@router.post(
    "/{slug}/synthesize",
    status_code=status.HTTP_200_OK,
    summary="Trigger LLM perspective synthesis for a topic",
)
def synthesize_topic_perspectives(
    slug: str,
    min_volume_threshold: int = Query(
        default=30,
        ge=2,
        description="Minimum usable discourse posts required before synthesis",
    ),
    db: Session = Depends(get_db),
):
    """Extract representative cluster samples and synthesize structured perspectives using LLM."""
    _enforce_rate_limit("public:synthesis")
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    pipeline = PerspectivePipeline()
    return pipeline.run_synthesis_for_topic(
        topic=topic,
        db=db,
        min_volume_threshold=min_volume_threshold,
    )


@router.post(
    "/{slug}/run-pipeline",
    status_code=status.HTTP_200_OK,
    summary="Execute complete end-to-end pipeline (Ingest -> Merge -> Cluster -> Synthesize)",
)
def run_full_pipeline(
    slug: str,
    limit_per_source: int = Query(default=50, ge=1, le=100),
    min_volume_threshold: int = Query(default=30, ge=2),
    db: Session = Depends(get_db),
):
    """Execute complete end-to-end pipeline: Ingestion into staging -> Merge into combined_raw_data -> HDBSCAN clustering -> LLM synthesis."""
    _enforce_rate_limit("public:pipeline")
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    # Enforce pipeline concurrency limit
    if not resource_governor.concurrency_governor.acquire("pipeline", holder_id=slug):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Maximum concurrent pipelines reached. Please try again later.",
            headers={"Retry-After": "30"},
        )

    clamped_limit = resource_governor.budget_manager.clamp("max_ingestion_items_per_source", limit_per_source)
    resource_governor.cost_tracker.record_pipeline_invocation()

    from app.core.telemetry import PipelineTimingTracker
    tracker = PipelineTimingTracker("end_to_end_pipeline", topic_slug=slug)

    try:
        # 1. Ingest & Merge
        with tracker.track("ingestion_and_merge"):
            ingestion_pipeline = IngestionPipeline()
            ingestion_res = ingestion_pipeline.run(topic=topic, db=db, limit_per_source=clamped_limit)

        # 2. HDBSCAN Cluster
        with tracker.track("hdbscan_clustering"):
            cluster_pipeline = ClusterPipeline()
            cluster_res = cluster_pipeline.run_for_topic(
                topic=topic,
                db=db,
                min_volume_threshold=min_volume_threshold,
            )

        # 3. LLM Synthesis
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

        # 4. Refresh trending score
        with tracker.track("trending_score_recalculation"):
            from app.workers.trending import TrendingScorer
            scorer = TrendingScorer()
            score_breakdown = scorer.calculate_topic_score(topic=topic, db=db)
            topic.trending_score = score_breakdown.final_score
            db.commit()
            db.refresh(topic)

        timings_summary = tracker.finish(status="success")

        return {
            "status": "success",
            "topic": {
                "id": topic.id,
                "slug": topic.slug,
                "title": topic.title,
                "trending_score": topic.trending_score,
                "source_coverage": topic.source_coverage,
                "perspectives_count": len(topic.perspectives) if topic.perspectives else 0,
            },
            "ingestion": ingestion_res,
            "clustering": cluster_res,
            "synthesis": synthesis_res,
            "timings": timings_summary,
        }
    except Exception:
        tracker.finish(status="failed")
        raise
    finally:
        resource_governor.concurrency_governor.release("pipeline", holder_id=slug)

