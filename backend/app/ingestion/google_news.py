from datetime import datetime
import email.utils
import html
import logging
import re
import urllib.parse
import urllib.request
from typing import List, Optional

import feedparser
from sqlalchemy.orm import Session

from app.database.models import RawGoogleNews, Topic

logger = logging.getLogger("app.ingestion.google_news")


def clean_html(raw_html: Optional[str]) -> str:
    """Strip HTML tags and unescape entities."""
    if not raw_html:
        return ""
    clean = re.sub(r"<[^>]+>", " ", raw_html)
    clean = html.unescape(clean)
    return re.sub(r"\s+", " ", clean).strip()


class GoogleNewsIngestor:
    """Ingests Google News RSS results using feedparser into the raw_google_news staging table."""

    source_name = "google_news"
    BASE_URL = "https://news.google.com/rss/search"

    def fetch_and_stage(
        self,
        topic: Topic,
        db: Session,
        limit: int = 50,
        timeout_seconds: float = 10.0,
    ) -> List[RawGoogleNews]:
        """Fetch RSS feed using feedparser, parse items, and stage them into raw_google_news."""
        logger.info(
            "Fetching [google_news] RSS for topic '%s' (ID %d, limit=%d, timeout=%.1fs)",
            topic.title,
            topic.id,
            limit,
            timeout_seconds,
        )

        params = urllib.parse.urlencode({
            "q": topic.title,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        })
        rss_url = f"{self.BASE_URL}?{params}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VantageNews/2.0"
        }
        req = urllib.request.Request(rss_url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                content = response.read()
        except Exception as e:
            logger.error("[google_news] Failed fetching RSS for '%s': %s", topic.title, str(e))
            return []

        return self.parse_and_persist(content, topic_id=topic.id, db=db, limit=limit)

    def parse_and_persist(
        self,
        xml_content: bytes,
        topic_id: int,
        db: Session,
        limit: int = 50,
    ) -> List[RawGoogleNews]:
        """Parse RSS content using feedparser and persist records to RawGoogleNews staging table."""
        feed = feedparser.parse(xml_content)
        entries = feed.entries or []

        staged_records: List[RawGoogleNews] = []
        for index, entry in enumerate(entries):
            if len(staged_records) >= limit:
                break
            try:
                title = clean_html(getattr(entry, "title", ""))
                link = getattr(entry, "link", None)
                snippet = clean_html(getattr(entry, "summary", "") or getattr(entry, "description", ""))

                # Extract source outlet name
                source_name = None
                if hasattr(entry, "source") and isinstance(entry.source, dict):
                    source_name = entry.source.get("title")
                elif hasattr(entry, "publisher"):
                    source_name = entry.publisher

                # Parse publication date
                published_at = datetime.utcnow()
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published_at = datetime(*entry.published_parsed[:6])
                elif hasattr(entry, "published"):
                    try:
                        parsed = email.utils.parsedate_to_datetime(entry.published)
                        if parsed:
                            published_at = parsed.replace(tzinfo=None)
                    except Exception:
                        pass

                if not title:
                    continue

                record = RawGoogleNews(
                    slug_id=topic_id,
                    title=title,
                    link=link,
                    source_name=source_name,
                    published_at=published_at,
                    snippet=snippet,
                )
                db.add(record)
                staged_records.append(record)
            except Exception as item_err:
                logger.warning("[google_news] Skipping malformed entry at #%d: %s", index, str(item_err))
                continue

        db.commit()
        logger.info("[google_news] Staged %d records for Topic ID %d", len(staged_records), topic_id)
        return staged_records
