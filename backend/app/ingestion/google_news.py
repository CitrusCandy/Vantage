from datetime import datetime
import email.utils
import html
import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import List, Optional

from app.ingestion.base import BaseIngestor
from app.ingestion.schemas import IngestedItem

logger = logging.getLogger("app.ingestion.google_news")


def clean_html(raw_html: Optional[str]) -> str:
    """Strip HTML tags and unescape HTML entities."""
    if not raw_html:
        return ""
    clean = re.sub(r"<[^>]+>", " ", raw_html)
    clean = html.unescape(clean)
    return re.sub(r"\s+", " ", clean).strip()


class GoogleNewsIngestor(BaseIngestor):
    """Ingests and normalizes news articles from Google News RSS feeds."""

    source_name = "google_news"
    BASE_URL = "https://news.google.com/rss/search"

    def fetch_items(
        self,
        query: str,
        limit: int = 25,
        timeout_seconds: float = 10.0,
    ) -> List[IngestedItem]:
        logger.info(
            "Source requested: [google_news] for query '%s' (limit=%d, timeout=%.1fs)",
            query,
            limit,
            timeout_seconds,
        )

        params = urllib.parse.urlencode({
            "q": query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        })
        rss_url = f"{self.BASE_URL}?{params}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VantageNews/1.0"
        }
        req = urllib.request.Request(rss_url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                xml_data = response.read()
        except urllib.error.URLError as e:
            logger.error(
                "Source failure/timeout: [google_news] failed fetching RSS feed for '%s': %s",
                query,
                str(e),
            )
            return []
        except Exception as e:
            logger.error(
                "Source failure: [google_news] unexpected error for '%s': %s",
                query,
                str(e),
            )
            return []

        return self.parse_rss_content(xml_data, limit=limit)

    def parse_rss_content(self, xml_content: bytes, limit: int = 25) -> List[IngestedItem]:
        """Parse raw XML content and normalize into IngestedItem models."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            logger.error("Source failure: [google_news] Failed parsing XML: %s", str(e))
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("[google_news] No <channel> element found in RSS feed")
            return []

        items = channel.findall("item")
        logger.info("Number of results received: [google_news] found %d items", len(items))

        normalized_items: List[IngestedItem] = []
        for index, item in enumerate(items):
            if len(normalized_items) >= limit:
                break
            try:
                title_elem = item.find("title")
                title_text = clean_html(title_elem.text) if title_elem is not None else ""

                link_elem = item.find("link")
                link_text = link_elem.text.strip() if link_elem is not None and link_elem.text else None

                desc_elem = item.find("description")
                desc_text = clean_html(desc_elem.text) if desc_elem is not None else ""

                source_elem = item.find("source")
                source_name = source_elem.text.strip() if source_elem is not None and source_elem.text else None

                # Extract publish date
                pub_date_elem = item.find("pubDate")
                created_at = datetime.utcnow()
                if pub_date_elem is not None and pub_date_elem.text:
                    try:
                        parsed_tuple = email.utils.parsedate_to_datetime(pub_date_elem.text)
                        if parsed_tuple:
                            created_at = parsed_tuple.replace(tzinfo=None)
                    except Exception:
                        pass

                # Build combined textual content
                text_parts = []
                if title_text:
                    text_parts.append(title_text)
                if desc_text and desc_text != title_text:
                    text_parts.append(desc_text)
                text_content = "\n\n".join(text_parts).strip()

                if not text_content:
                    logger.debug("[google_news] Skipping item #%d due to empty text content", index)
                    continue

                ingested = IngestedItem(
                    source=self.source_name,
                    text_content=text_content,
                    url=link_text,
                    author_handle=source_name or "Google News Outlet",
                    engagement_metrics={"publisher": source_name} if source_name else {},
                    created_at=created_at,
                )
                normalized_items.append(ingested)
            except Exception as item_err:
                logger.warning(
                    "[google_news] Malformed entry skipped at index #%d: %s",
                    index,
                    str(item_err),
                )
                continue

        logger.info(
            "Number successfully normalized: [google_news] %d / %d items",
            len(normalized_items),
            len(items),
        )
        return normalized_items
