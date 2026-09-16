from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.database.models import RawData, Topic
from app.ingestion.base import BaseIngestor
from app.ingestion.google_news import GoogleNewsIngestor
from app.ingestion.reddit import RedditIngestor
from app.ingestion.schemas import IngestedItem

logger = logging.getLogger("app.ingestion.pipeline")


class IngestionPipeline:
    """Orchestrates multi-source data ingestion, normalization, and persistence."""

    def __init__(self, ingestors: Optional[List[BaseIngestor]] = None):
        if ingestors is None:
            self.ingestors: List[BaseIngestor] = [
                GoogleNewsIngestor(),
                RedditIngestor(),
            ]
        else:
            self.ingestors = ingestors

    def run(
        self,
        topic: Topic,
        db: Session,
        limit_per_source: int = 25,
        per_source_timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """Execute parallel ingestion across all registered sources for the given topic."""
        logger.info(
            "Ingestion started: Topic ID %d ('%s') with %d sources",
            topic.id,
            topic.title,
            len(self.ingestors),
        )

        all_items: List[IngestedItem] = []
        source_counts: Dict[str, int] = {}
        source_errors: Dict[str, str] = {}

        # Fan out concurrently using thread pool
        with ThreadPoolExecutor(max_workers=len(self.ingestors) or 1) as executor:
            future_to_source = {
                executor.submit(
                    ingestor.fetch_items,
                    query=topic.title,
                    limit=limit_per_source,
                    timeout_seconds=per_source_timeout,
                ): ingestor.source_name
                for ingestor in self.ingestors
            }

            for future in as_completed(future_to_source):
                source_name = future_to_source[future]
                try:
                    items = future.result()
                    source_counts[source_name] = len(items)
                    all_items.extend(items)
                    logger.info(
                        "Source completed: [%s] provided %d normalized items",
                        source_name,
                        len(items),
                    )
                except Exception as e:
                    source_errors[source_name] = str(e)
                    source_counts[source_name] = 0
                    logger.error(
                        "Source failure/timeout: [%s] failed during pipeline execution: %s",
                        source_name,
                        str(e),
                    )

        # Persist items to database as RawData records
        persisted_records: List[RawData] = []
        for item in all_items:
            raw_record = RawData(
                topic_id=topic.id,
                source=item.source,
                text_content=item.text_content,
                url=item.url,
                author_handle=item.author_handle,
                engagement_metrics=item.engagement_metrics,
                is_flagged_bot=False,
                created_at=item.created_at,
            )
            persisted_records.append(raw_record)
            db.add(raw_record)

        # Update topic metadata
        existing_coverage = topic.source_coverage or {}
        if not isinstance(existing_coverage, dict):
            existing_coverage = {}

        updated_coverage = dict(existing_coverage)
        for source_name, count in source_counts.items():
            updated_coverage[source_name] = updated_coverage.get(source_name, 0) + count
        updated_coverage["total_raw_items"] = (
            updated_coverage.get("total_raw_items", 0) + len(persisted_records)
        )

        topic.source_coverage = updated_coverage
        topic.search_count = (topic.search_count or 0) + 1
        topic.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(topic)

        logger.info(
            "Number persisted: successfully saved %d items for topic '%s' (ID %d). Search count: %d",
            len(persisted_records),
            topic.title,
            topic.id,
            topic.search_count,
        )

        return {
            "topic_id": topic.id,
            "topic_title": topic.title,
            "total_persisted": len(persisted_records),
            "source_breakdown": source_counts,
            "source_errors": source_errors,
            "source_coverage": topic.source_coverage,
        }
