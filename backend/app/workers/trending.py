from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.database.models import CombinedRawData, Topic
from app.workers.worker_config import WorkerConfig, get_worker_config

logger = logging.getLogger("app.workers.trending")


@dataclass
class TrendingBreakdown:
    mention_velocity: float
    unique_source_count: int
    normalized_sources: float
    engagement_rate: float
    time_decay: float
    raw_score: float
    final_score: float


class TrendingScorer:
    """Calculates multi-dimensional trending scores for discourse topics."""

    def __init__(self, config: Optional[WorkerConfig] = None):
        self.config = config or get_worker_config()

    def compute_score(
        self,
        mention_velocity: float,
        unique_source_count: int,
        engagement_rate: float,
        hours_since_update: float,
    ) -> TrendingBreakdown:
        """Compute normalized trending score using configurable formula."""
        # 1. Normalize unique sources (0 to 3 sources -> 0.0 to 1.0)
        norm_sources = min(1.0, max(0.0, unique_source_count / 3.0))

        # 2. Bound velocity and engagement to [0.0, 1.0]
        vel = min(1.0, max(0.0, mention_velocity))
        eng = min(1.0, max(0.0, engagement_rate))

        # 3. Calculate time decay factor based on exponential half-life
        half_life = max(1.0, self.config.decay_half_life_hours)
        time_decay = 1.0 - math.exp(-max(0.0, hours_since_update) / half_life)
        time_decay = min(1.0, max(0.0, time_decay))

        # 4. Compute weighted score
        raw_score = (
            (self.config.w1_mention_velocity * vel)
            + (self.config.w2_unique_sources * norm_sources)
            + (self.config.w3_engagement_rate * eng)
            - (self.config.w4_time_decay * time_decay)
        )

        final_score = round(max(0.0, min(1.0, raw_score)), 4)

        return TrendingBreakdown(
            mention_velocity=vel,
            unique_source_count=unique_source_count,
            normalized_sources=norm_sources,
            engagement_rate=eng,
            time_decay=time_decay,
            raw_score=round(raw_score, 4),
            final_score=final_score,
        )

    def calculate_topic_score(
        self,
        topic: Topic,
        db: Session,
        reference_time: Optional[datetime] = None,
    ) -> TrendingBreakdown:
        """Evaluate database records for a topic and calculate its live trending score."""
        now = reference_time or datetime.utcnow()

        # Query all non-bot combined records for this topic
        records: List[CombinedRawData] = (
            db.query(CombinedRawData)
            .filter(
                CombinedRawData.slug_id == topic.id,
                CombinedRawData.is_flagged_bot == False,  # noqa: E712
            )
            .all()
        )

        if not records:
            # Baseline score for newly created topics without records yet
            hours_since_creation = (
                (now - topic.updated_at).total_seconds() / 3600.0
                if topic.updated_at
                else 0.0
            )
            return self.compute_score(
                mention_velocity=0.0,
                unique_source_count=0,
                engagement_rate=0.0,
                hours_since_update=hours_since_creation,
            )

        # 1. Mention Velocity: Ratio of items in last 24h vs total (or volume per 24h)
        cutoff_24h = now - timedelta(hours=24)
        recent_records = [r for r in records if r.created_at and r.created_at >= cutoff_24h]
        # Normalize velocity: 50+ recent records is considered max velocity (1.0)
        mention_velocity = min(1.0, len(recent_records) / 50.0) if recent_records else 0.0

        # 2. Unique Sources: Count distinct active platforms
        unique_sources = len(set(r.source for r in records if r.source))

        # 3. Engagement Rate: Sum of platform engagement metrics
        total_engagement = 0
        for r in records:
            metrics = r.engagement_metrics or {}
            if isinstance(metrics, dict):
                total_engagement += int(metrics.get("score", 0))
                total_engagement += int(metrics.get("likes", 0))
                total_engagement += int(metrics.get("retweets", 0))
                total_engagement += int(metrics.get("num_comments", 0))

        # Log scale normalization for engagement
        engagement_rate = min(1.0, math.log10(max(1, total_engagement) + 1) / 4.0)

        # 4. Time Decay: Hours since latest recorded discourse item
        latest_record_time = max(
            (r.created_at for r in records if r.created_at),
            default=topic.updated_at or now,
        )
        hours_since_latest = max(0.0, (now - latest_record_time).total_seconds() / 3600.0)

        return self.compute_score(
            mention_velocity=mention_velocity,
            unique_source_count=unique_sources,
            engagement_rate=engagement_rate,
            hours_since_update=hours_since_latest,
        )
