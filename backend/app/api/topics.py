from datetime import datetime
import re
from typing import List, Optional
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
        "reddit": 0,
        "google_news": 0,
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
    """Safely delete a topic and its associated child records (perspectives, raw data, cluster runs)."""
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    db.delete(topic)
    db.commit()
    return {"message": f"Topic '{slug}' and its associated records have been deleted successfully"}
