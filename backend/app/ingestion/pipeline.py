from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import resource_governor
from app.core.resilience import CancellationToken
from app.core.telemetry import PipelineTimingTracker
from app.database.database import SessionLocal
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
        cancellation_token: Optional[CancellationToken] = None,
    ) -> Dict[str, Any]:
        """Run all independent source scrapers concurrently into staging, then merge into combined_raw_data."""
        if cancellation_token:
            cancellation_token.raise_if_cancelled()

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

        def _run_source_isolated(source_name: str, fetch_fn):
            """Wrapper creating an isolated database session per thread to ensure thread safety."""
            if cancellation_token and cancellation_token.is_cancelled:
                logger.info("Scraper [%s] skipped: cancellation requested", source_name)
                return []

            allowed, reason = resource_governor.external_governor.check_request_allowed(source_name)
            if not allowed:
                logger.warning("External request blocked for %s: %s", source_name, reason)
                return []

            resource_governor.external_governor.record_request_start(source_name)
            resource_governor.cost_tracker.record_external_request(source_name)

            # Thread-isolated session
            thread_db: Optional[Session] = None
            try:
                from app.database import database
                thread_db = database.SessionLocal()
                # Attach topic to thread session
                thread_topic = thread_db.merge(topic)
                return fetch_fn(thread_topic, thread_db)
            except Exception as e:
                logger.error("Scraper isolated execution error for [%s]: %s", source_name, str(e))
                if thread_db:
                    thread_db.rollback()
                raise
            finally:
                if thread_db:
                    thread_db.close()
                resource_governor.external_governor.record_request_end(source_name)

        # 1. Independent Scrapers Fan-Out to Staging Tables
        with tracker.track("fanout_staging_ingestion", limit_per_source=clamped_limit):
            tasks = [
                ("google_news", lambda t, s: self.google_news_ingestor.fetch_and_stage(t, s, limit=clamped_limit, timeout_seconds=per_source_timeout)),
                ("reddit", lambda t, s: self.reddit_ingestor.fetch_and_stage(t, s, limit=clamped_limit, timeout_seconds=per_source_timeout)),
                ("x", lambda t, s: self.x_scraper.fetch_and_stage(t, s, limit=clamped_limit, timeout_seconds=per_source_timeout)),
            ]

            max_workers = min(len(tasks), resource_governor.budget_manager.get_limit("max_concurrent_source_calls"))
            with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
                future_to_source = {
                    executor.submit(_run_source_isolated, name, fn): name
                    for name, fn in tasks
                }
                for future in as_completed(future_to_source):
                    source_name = future_to_source[future]
                    try:
                        records = future.result()
                        staging_counts[source_name] = len(records)
                    except Exception as e:
                        staging_errors[source_name] = str(e)
                        staging_counts[source_name] = 0
                        logger.error("Scraper failed for [%s]: %s", source_name, str(e))

        if cancellation_token:
            cancellation_token.raise_if_cancelled()

        # Increment topic search count
        topic.search_count = (topic.search_count or 0) + 1
        db.commit()

        # 2. Merge Stage: Staging Tables -> combined_raw_data (fail-soft with partial results)
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
