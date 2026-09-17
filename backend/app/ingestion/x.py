from datetime import datetime
import logging
import re
import time
from typing import List, Optional

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.database.models import RawX, Topic

logger = logging.getLogger("app.ingestion.x")


class XScraper:
    """Scrapes and parses public discourse from X (Twitter) into the raw_x staging table."""

    source_name = "x"

    def __init__(self, max_retries: int = 2, backoff_seconds: float = 1.0):
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def parse_html_and_persist(
        self,
        html_content: str,
        topic_id: int,
        db: Session,
        limit: int = 50,
    ) -> List[RawX]:
        """Parse raw HTML / rendered DOM from X and stage records into RawX table."""
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        # Support both standard tweet containers and article tags
        tweet_elements = soup.find_all("article") or soup.find_all("div", class_=re.compile(r"tweet|post"))

        staged_records: List[RawX] = []
        for index, elem in enumerate(tweet_elements):
            if len(staged_records) >= limit:
                break
            try:
                # 1. Extract tweet text
                text_elem = elem.find("div", {"data-testid": "tweetText"}) or elem.find("div", class_=re.compile(r"text|content"))
                text = text_elem.get_text(separator=" ", strip=True) if text_elem else elem.get_text(separator=" ", strip=True)

                if not text or len(text) < 10:
                    continue

                # 2. Extract user handle
                handle = None
                user_elem = elem.find("div", {"data-testid": "User-Name"}) or elem.find("span", text=re.compile(r"^@\w+"))
                if user_elem:
                    handle_match = re.search(r"@(\w+)", user_elem.get_text())
                    if handle_match:
                        handle = handle_match.group(1)

                # 3. Extract tweet ID / URL link
                tweet_id = None
                link_elem = elem.find("a", href=re.compile(r"/status/(\d+)"))
                if link_elem and "href" in link_elem.attrs:
                    id_match = re.search(r"/status/(\d+)", link_elem["href"])
                    if id_match:
                        tweet_id = id_match.group(1)

                if not tweet_id:
                    tweet_id = f"mock_tx_{topic_id}_{index}_{int(time.time())}"

                # 4. Extract engagement metrics (likes, retweets, replies)
                likes = self._extract_metric(elem, "like")
                retweets = self._extract_metric(elem, "retweet")
                replies = self._extract_metric(elem, "reply")

                # 5. Extract timestamp
                time_elem = elem.find("time")
                posted_at = datetime.utcnow()
                if time_elem and time_elem.get("datetime"):
                    try:
                        posted_at = datetime.fromisoformat(time_elem["datetime"].replace("Z", "+00:00")).replace(tzinfo=None)
                    except Exception:
                        pass

                record = RawX(
                    slug_id=topic_id,
                    tweet_id=tweet_id,
                    text=text,
                    likes=likes,
                    retweets=retweets,
                    replies=replies,
                    handle=handle or "anonymous",
                    posted_at=posted_at,
                )
                db.add(record)
                staged_records.append(record)
            except Exception as e:
                logger.warning("[x] Error parsing tweet element #%d: %s", index, str(e))
                continue

        db.commit()
        logger.info("[x] Staged %d records for Topic ID %d", len(staged_records), topic_id)
        return staged_records

    @staticmethod
    def _extract_metric(elem, test_id_substr: str) -> int:
        """Helper to extract metric integers from data-testid or aria-labels."""
        target = elem.find(attrs={"data-testid": re.compile(test_id_substr, re.I)}) or elem.find(attrs={"aria-label": re.compile(test_id_substr, re.I)})
        if target:
            text = target.get_text(strip=True) or target.get("aria-label", "")
            match = re.search(r"(\d+(?:,\d+)*(?:\.\d+)?[kKmM]?)", text)
            if match:
                val_str = match.group(1).replace(",", "")
                if val_str.lower().endswith("k"):
                    return int(float(val_str[:-1]) * 1000)
                elif val_str.lower().endswith("m"):
                    return int(float(val_str[:-1]) * 1000000)
                try:
                    return int(float(val_str))
                except ValueError:
                    pass
        return 0

    def fetch_and_stage(
        self,
        topic: Topic,
        db: Session,
        limit: int = 50,
        timeout_seconds: float = 10.0,
    ) -> List[RawX]:
        """Scrape X with fail-soft isolation. Never throws to downstream pipeline."""
        from app.core.resilience import BackoffStrategy, JitterMode, x_breaker, retry_with_backoff

        if not x_breaker.allow_request():
            logger.warning("[x] Circuit breaker is OPEN. Short-circuiting request for topic '%s'", topic.title)
            return []

        logger.info(
            "Fetching [x] posts for topic '%s' (ID %d, limit=%d, timeout=%.1fs)",
            topic.title,
            topic.id,
            limit,
            timeout_seconds,
        )

        backoff = BackoffStrategy(base_delay=self.backoff_seconds, max_delay=5.0, multiplier=2.0, jitter_mode=JitterMode.FULL)
        
        def _scrape_attempt():
            # Fail-soft network execution / Selenium browser session placeholder
            # Without credentials/live browser in testing/offline, safely returns empty without crashing
            return []

        scrape_with_retries = retry_with_backoff(
            max_attempts=self.max_retries,
            backoff=backoff,
            reraise_last=True,
        )(_scrape_attempt)

        try:
            return x_breaker.execute(scrape_with_retries)
        except Exception as e:
            logger.error("[x] Fail-soft: X ingestion failed gracefully for topic '%s': %s", topic.title, str(e))
            return []
