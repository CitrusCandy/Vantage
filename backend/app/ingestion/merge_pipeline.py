from datetime import datetime
import logging
from typing import Any, Dict, List, Set, Tuple

from sqlalchemy.orm import Session

from app.core import resource_governor
from app.core.security import sanitize_url
from app.database.models import (
    CombinedRawData,
    RawGoogleNews,
    RawReddit,
    RawX,
    Topic,
)

logger = logging.getLogger("app.ingestion.merge_pipeline")


class MergePipeline:
    """ETL stage reading from staging tables (raw_google_news, raw_reddit, raw_x) and mapping to combined_raw_data."""

    def merge_topic_staging_data(
        self,
        topic: Topic,
        db: Session,
    ) -> Dict[str, Any]:
        """Merge all staging records for a topic into combined_raw_data, preventing duplicate inserts."""
        logger.info("Merge started for Topic ID %d ('%s')", topic.id, topic.title)

        # 1. Fetch existing combined records for idempotency check
        existing_combined = (
            db.query(CombinedRawData)
            .filter(CombinedRawData.slug_id == topic.id)
            .all()
        )

        # Build fingerprint set (source, url or text_prefix) to avoid duplicate rows on repeated merge runs
        existing_fingerprints: Set[Tuple[str, str]] = {
            (c.source, c.url or c.text_content[:100]) for c in existing_combined
        }

        new_combined_records: List[CombinedRawData] = []
        source_breakdown = {"google_news": 0, "reddit": 0, "x": 0}

        # 2. Ingest from raw_google_news
        gn_records = (
            db.query(RawGoogleNews)
            .filter(RawGoogleNews.slug_id == topic.id)
            .all()
        )
        for gn in gn_records:
            text_parts = [gn.title]
            if gn.snippet and gn.snippet != gn.title:
                text_parts.append(gn.snippet)
            full_text = "\n\n".join(text_parts).strip()
            fp = ("google_news", gn.link or full_text[:100])
            if fp in existing_fingerprints:
                continue

            record = CombinedRawData(
                slug_id=topic.id,
                source="google_news",
                text_content=full_text,
                url=sanitize_url(gn.link),
                author_handle=gn.source_name,
                engagement_metrics={"source_name": gn.source_name} if gn.source_name else {},
                is_flagged_bot=False,
                created_at=gn.published_at,
            )
            new_combined_records.append(record)
            existing_fingerprints.add(fp)
            source_breakdown["google_news"] += 1
            db.add(record)

        # 3. Ingest from raw_reddit
        reddit_records = (
            db.query(RawReddit)
            .filter(RawReddit.slug_id == topic.id)
            .all()
        )
        for rd in reddit_records:
            url = sanitize_url(f"https://www.reddit.com/comments/{rd.post_id}") if rd.post_id else None
            fp = ("reddit", url or rd.body[:100])
            if fp in existing_fingerprints:
                continue

            record = CombinedRawData(
                slug_id=topic.id,
                source="reddit",
                text_content=rd.body,
                url=url,
                author_handle=f"u/{rd.author}" if rd.author else None,
                engagement_metrics={
                    "score": rd.score,
                    "num_comments": rd.num_comments,
                    "subreddit": rd.subreddit,
                },
                is_flagged_bot=False,
                created_at=rd.created_utc,
            )
            new_combined_records.append(record)
            existing_fingerprints.add(fp)
            source_breakdown["reddit"] += 1
            db.add(record)

        # 4. Ingest from raw_x
        x_records = (
            db.query(RawX)
            .filter(RawX.slug_id == topic.id)
            .all()
        )
        for x in x_records:
            raw_x_url = f"https://x.com/{x.handle}/status/{x.tweet_id}" if x.handle and x.tweet_id else None
            url = sanitize_url(raw_x_url)
            fp = ("x", url or x.text[:100])
            if fp in existing_fingerprints:
                continue

            record = CombinedRawData(
                slug_id=topic.id,
                source="x",
                text_content=x.text,
                url=url,
                author_handle=f"@{x.handle}" if x.handle else None,
                engagement_metrics={
                    "likes": x.likes,
                    "retweets": x.retweets,
                    "replies": x.replies,
                },
                is_flagged_bot=False,
                created_at=x.posted_at,
            )
            new_combined_records.append(record)
            existing_fingerprints.add(fp)
            source_breakdown["x"] += 1
            db.add(record)

        # 5. Enforce merged items budget
        max_combined = resource_governor.budget_manager.get_limit("max_merged_items_per_topic")
        actual_total = len(existing_combined) + len(new_combined_records)
        if actual_total > max_combined and new_combined_records:
            allowed_new = max(0, max_combined - len(existing_combined))
            if allowed_new < len(new_combined_records):
                logger.warning(
                    "Truncating merged items for topic '%s': %d -> %d (budget: %d total)",
                    topic.title,
                    len(new_combined_records),
                    allowed_new,
                    max_combined,
                )
                # Remove excess records that were already added to the session
                for excess_record in new_combined_records[allowed_new:]:
                    db.expunge(excess_record)
                new_combined_records = new_combined_records[:allowed_new]

        # 6. Update Topic metadata
        total_combined = len(existing_combined) + len(new_combined_records)
        existing_coverage = topic.source_coverage or {}
        if not isinstance(existing_coverage, dict):
            existing_coverage = {}

        updated_coverage = dict(existing_coverage)
        for src, count in source_breakdown.items():
            updated_coverage[src] = updated_coverage.get(src, 0) + count
        updated_coverage["total_combined"] = total_combined

        topic.source_coverage = updated_coverage
        topic.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(topic)

        logger.info(
            "Merge finished for topic '%s': +%d new records added (%s). Total combined: %d",
            topic.title,
            len(new_combined_records),
            source_breakdown,
            total_combined,
        )

        return {
            "status": "success",
            "topic_id": topic.id,
            "new_records_added": len(new_combined_records),
            "source_breakdown": source_breakdown,
            "total_combined_count": total_combined,
            "source_coverage": topic.source_coverage,
        }
