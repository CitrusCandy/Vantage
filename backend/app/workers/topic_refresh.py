from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.database.models import Topic
from app.ingestion.pipeline import IngestionPipeline
from app.llm.pipeline import PerspectivePipeline
from app.processing.cluster_pipeline import ClusterPipeline
from app.workers.discovery import CandidateTopic, TrendDiscoveryService
from app.workers.trending import TrendingScorer
from app.workers.worker_config import WorkerConfig, get_worker_config

logger = logging.getLogger("app.workers.topic_refresh")


@dataclass
class RefreshSummary:
    total_topics_evaluated: int = 0
    scores_updated: int = 0
    refreshed_topics: List[str] = field(default_factory=list)
    skipped_stagnant_topics: List[str] = field(default_factory=list)
    new_candidates_created: List[str] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)


class TopicRefreshWorker:
    """Orchestrates trending score recalculation, stagnant topic filtering, and automated pipeline execution."""

    def __init__(
        self,
        config: Optional[WorkerConfig] = None,
        scorer: Optional[TrendingScorer] = None,
        discovery_service: Optional[TrendDiscoveryService] = None,
        ingestion_pipeline: Optional[IngestionPipeline] = None,
        cluster_pipeline: Optional[ClusterPipeline] = None,
        perspective_pipeline: Optional[PerspectivePipeline] = None,
    ):
        self.config = config or get_worker_config()
        self.scorer = scorer or TrendingScorer(self.config)
        self.discovery_service = discovery_service or TrendDiscoveryService()
        self.ingestion_pipeline = ingestion_pipeline or IngestionPipeline()
        self.cluster_pipeline = cluster_pipeline or ClusterPipeline()
        self.perspective_pipeline = perspective_pipeline or PerspectivePipeline()

    def recalculate_all_trending_scores(self, db: Session) -> int:
        """Update trending_score for every topic in the database based on recent activity."""
        topics = db.query(Topic).all()
        updated_count = 0
        for topic in topics:
            score_breakdown = self.scorer.calculate_topic_score(topic=topic, db=db)
            topic.trending_score = score_breakdown.final_score
            updated_count += 1
        db.commit()
        logger.info("Recalculated trending scores for %d topics", updated_count)
        return updated_count

    def run_refresh_cycle(
        self,
        db: Session,
        auto_discover: bool = False,
        min_volume_threshold: int = 30,
        force_refresh_all: bool = False,
    ) -> RefreshSummary:
        """Execute one complete background refresh cycle."""
        logger.info("Starting topic refresh worker cycle")
        summary = RefreshSummary()

        # Step 1: Optional Trend Discovery & Topic Auto-Enrollment
        if auto_discover:
            try:
                candidates = self.discovery_service.discover_trending_topics()
                for cand in candidates:
                    existing = db.query(Topic).filter(Topic.title.ilike(cand.title)).first()
                    if not existing:
                        from app.api.topics import generate_slug, get_unique_slug
                        slug = get_unique_slug(db, generate_slug(cand.title))
                        new_topic = Topic(
                            title=cand.title,
                            slug=slug,
                            search_count=1,
                            trending_score=0.5,
                            source_coverage={"google_news": 0, "reddit": 0, "x": 0, "total_combined": 0},
                            updated_at=datetime.utcnow(),
                        )
                        db.add(new_topic)
                        db.commit()
                        db.refresh(new_topic)
                        summary.new_candidates_created.append(new_topic.slug)
            except Exception as e:
                logger.error("Auto-discovery step failed: %s", str(e))
                summary.errors["auto_discover"] = str(e)

        # Step 2: Recalculate all trending scores
        topics: List[Topic] = db.query(Topic).all()
        summary.total_topics_evaluated = len(topics)

        now = datetime.utcnow()
        for topic in topics:
            try:
                score_info = self.scorer.calculate_topic_score(topic=topic, db=db, reference_time=now)
                topic.trending_score = score_info.final_score
                summary.scores_updated += 1

                # Check if stagnant (no recent updates and low score)
                hours_since_update = (
                    (now - topic.updated_at).total_seconds() / 3600.0
                    if topic.updated_at
                    else 999.0
                )

                is_stagnant = (
                    hours_since_update > self.config.stagnant_hours_threshold
                    and topic.trending_score < self.config.min_trending_score_for_refresh
                )

                if is_stagnant and not force_refresh_all:
                    summary.skipped_stagnant_topics.append(topic.slug)
                    logger.debug("Skipping stagnant topic '%s' (score=%.3f, idle=%.1fh)", topic.slug, topic.trending_score, hours_since_update)
                    continue

                # Check if eligible for full pipeline refresh
                should_refresh = (
                    force_refresh_all
                    or topic.trending_score >= self.config.min_trending_score_for_refresh
                    or topic.last_clustered_at is None
                )

                if should_refresh:
                    logger.info("Refreshing topic '%s' (trending score: %.3f)", topic.slug, topic.trending_score)
                    # 1. Ingestion -> Staging -> Merge
                    self.ingestion_pipeline.run(topic=topic, db=db)
                    # 2. Clustering
                    cluster_res = self.cluster_pipeline.run_for_topic(
                        topic=topic,
                        db=db,
                        min_volume_threshold=min_volume_threshold,
                    )
                    # 3. LLM Perspective Synthesis (if volume permits)
                    if cluster_res.get("status") == "success":
                        self.perspective_pipeline.run_synthesis_for_topic(
                            topic=topic,
                            db=db,
                            min_volume_threshold=min_volume_threshold,
                            cluster_data=cluster_res,
                        )
                    summary.refreshed_topics.append(topic.slug)

            except Exception as e:
                logger.error("Error processing topic '%s': %s", topic.slug, str(e))
                summary.errors[topic.slug] = str(e)

        db.commit()
        logger.info(
            "Topic refresh cycle completed: %d evaluated, %d scores updated, %d refreshed, %d stagnant skipped",
            summary.total_topics_evaluated,
            summary.scores_updated,
            len(summary.refreshed_topics),
            len(summary.skipped_stagnant_topics),
        )
        return summary

    def refresh_topic(
        self,
        topic: Topic,
        db: Session,
        min_volume_threshold: int = 30,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """Refresh a single topic: score update, ingestion, clustering, and synthesis."""
        score_info = self.scorer.calculate_topic_score(topic=topic, db=db)
        topic.trending_score = score_info.final_score

        # 1. Ingestion
        ingest_res = self.ingestion_pipeline.run(topic=topic, db=db)

        # 2. Clustering
        cluster_res = self.cluster_pipeline.run_for_topic(
            topic=topic,
            db=db,
            min_volume_threshold=min_volume_threshold,
        )

        # 3. Perspective Synthesis
        synthesis_res = None
        if cluster_res.get("status") == "success":
            synthesis_res = self.perspective_pipeline.run_synthesis_for_topic(
                topic=topic,
                db=db,
                min_volume_threshold=min_volume_threshold,
                cluster_data=cluster_res,
            )

        db.commit()
        db.refresh(topic)

        return {
            "status": "success",
            "topic_slug": topic.slug,
            "trending_score": topic.trending_score,
            "ingestion": ingest_res,
            "clustering": cluster_res,
            "synthesis": synthesis_res,
        }
