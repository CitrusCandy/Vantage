from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import resource_governor
from app.core.telemetry import PipelineTimingTracker
from app.database.models import Topic
from app.ingestion.google_news import GoogleNewsIngestor
from app.ingestion.merge_pipeline import MergePipeline
from app.ingestion.reddit import RedditIngestor
from app.ingestion.x import XScraper

logger = logging.getLogger("app.ingestion.pipeline")


class IngestionPipeline:
    """Orchestrates independent multi-source scrapers to staging tables, followed by merging into combined_raw_data."""

    def __init__(
        self,
        google_news_ingestor: Optional[GoogleNewsIngestor] = None,
        reddit_ingestor: Optional[RedditIngestor] = None,
        x_scraper: Optional[XScraper] = None,
        merge_pipeline: Optional[MergePipeline] = None,
    ):
        self.google_news_ingestor = google_news_ingestor or GoogleNewsIngestor()
        self.reddit_ingestor = reddit_ingestor or RedditIngestor()
        self.x_scraper = x_scraper or XScraper()
        self.merge_pipeline = merge_pipeline or MergePipeline()

    def run(
        self,
        topic: Topic,
        db: Session,
        limit_per_source: int = 50,
        per_source_timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """Run all independent source scrapers concurrently into staging, then merge into combined_raw_data."""
        logger.info(
            "Ingestion pipeline started: Topic ID %d ('%s')",
            topic.id,
            topic.title,
        )
        tracker = PipelineTimingTracker("ingestion_pipeline")

        # Clamp limit_per_source to budget
        clamped_limit = resource_governor.budget_manager.clamp("max_ingestion_items_per_source", limit_per_source)

        staging_counts: Dict[str, int] = {}
        staging_errors: Dict[str, str] = {}

        def _run_source(source_name, fn):
            """Wrapper that tracks external requests and governs concurrency."""
            allowed, reason = resource_governor.external_governor.check_request_allowed(source_name)
            if not allowed:
                logger.warning("External request blocked for %s: %s", source_name, reason)
                return []
            resource_governor.external_governor.record_request_start(source_name)
            resource_governor.cost_tracker.record_external_request(source_name)
            try:
                return fn()
            finally:
                resource_governor.external_governor.record_request_end(source_name)

        # 1. Independent Scrapers Fan-Out to Staging Tables
        with tracker.track("fanout_staging_ingestion", limit_per_source=clamped_limit):
            tasks = [
                ("google_news", lambda: _run_source("google_news", lambda: self.google_news_ingestor.fetch_and_stage(topic, db, limit=clamped_limit, timeout_seconds=per_source_timeout))),
                ("reddit", lambda: _run_source("reddit", lambda: self.reddit_ingestor.fetch_and_stage(topic, db, limit=clamped_limit, timeout_seconds=per_source_timeout))),
                ("x", lambda: _run_source("x", lambda: self.x_scraper.fetch_and_stage(topic, db, limit=clamped_limit, timeout_seconds=per_source_timeout))),
            ]

            max_workers = min(len(tasks), resource_governor.budget_manager.get_limit("max_concurrent_source_calls"))
            with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
                future_to_source = {executor.submit(fn): name for name, fn in tasks}
                for future in as_completed(future_to_source):
                    source_name = future_to_source[future]
                    try:
                        records = future.result()
                        staging_counts[source_name] = len(records)
                    except Exception as e:
                        staging_errors[source_name] = str(e)
                        staging_counts[source_name] = 0
                        logger.error("Scraper failed for [%s]: %s", source_name, str(e))

        # Increment topic search count
        topic.search_count = (topic.search_count or 0) + 1
        db.commit()

        # 2. Merge Stage: Staging Tables -> combined_raw_data
        with tracker.track("merge_and_normalization"):
            merge_result = self.merge_pipeline.merge_topic_staging_data(topic=topic, db=db)

        return {
            "topic_id": topic.id,
            "topic_title": topic.title,
            "staging_counts": staging_counts,
            "staging_errors": staging_errors,
            "merge_result": merge_result,
            "timings": tracker.get_summary(),
        }
