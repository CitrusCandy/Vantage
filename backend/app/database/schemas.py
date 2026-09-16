from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TopicBase(BaseModel):
    title: str = Field(..., description="Topic title")
    slug: str = Field(..., description="Unique URL slug")
    source_coverage: Optional[Dict[str, Any]] = Field(default=None, description="Coverage statistics per source")


class TopicCreate(BaseModel):
    title: str = Field(..., description="Topic title or search query")
    slug: Optional[str] = Field(default=None, description="Optional custom URL slug")
    source_coverage: Optional[Dict[str, Any]] = Field(default=None, description="Initial source coverage dictionary")


class TopicUpdate(BaseModel):
    title: Optional[str] = Field(default=None, description="Updated topic title")
    source_coverage: Optional[Dict[str, Any]] = Field(default=None, description="Updated source coverage dictionary")


class TopicResponse(TopicBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    search_count: int
    trending_score: float
    last_clustered_at: Optional[datetime] = None
    updated_at: datetime


class PerspectiveResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    per_id: int
    topic_id: int
    perspective_type: str
    estimated_share: Optional[float] = None
    summary_points: Optional[Any] = None
    sample_quotes: Optional[Any] = None
    confidence_note: Optional[str] = None
    generated_at: datetime


class RawDataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    raw_id: int
    topic_id: int
    source: str
    text_content: str
    url: Optional[str] = None
    author_handle: Optional[str] = None
    engagement_metrics: Optional[Dict[str, Any]] = None
    is_flagged_bot: bool
    created_at: datetime


class ClusterRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: int
    topic_id: int
    cluster_algorithm: str
    cluster_count: int
    sample_size: int
    run_at: datetime


class TopicDetailResponse(TopicResponse):
    perspectives: List[PerspectiveResponse] = []
