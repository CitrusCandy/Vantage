from datetime import datetime
import re
from typing import Any, Dict, List, Optional
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

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
from app.processing.cluster_pipeline import ClusterPipeline

router = APIRouter(prefix="/topics", tags=["Topics"])


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
        search_term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Topic.title.ilike(search_term),
                Topic.slug.ilike(search_term),
            )
        )
    return query.order_by(Topic.updated_at.desc()).all()


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
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    pipeline = IngestionPipeline()
    return pipeline.run(topic=topic, db=db, limit_per_source=limit_per_source)


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
