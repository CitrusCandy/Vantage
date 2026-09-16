from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class IngestedItem(BaseModel):
    """Common normalized schema for an ingested public-discourse item."""

    model_config = ConfigDict(from_attributes=True)

    source: str = Field(..., description="Source origin, e.g., 'google_news', 'reddit'")
    text_content: str = Field(..., description="Main text content / headline / snippet")
    url: Optional[str] = Field(default=None, description="URL reference to original content")
    author_handle: Optional[str] = Field(default=None, description="Author or publication name")
    engagement_metrics: Dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific engagement metrics",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when the item was published/created",
    )
