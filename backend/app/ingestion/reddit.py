from datetime import datetime
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional

import praw
from sqlalchemy.orm import Session

from app.core.telemetry import ops_metrics
from app.database.models import RawReddit, Topic

logger = logging.getLogger("app.ingestion.reddit")


class RedditIngestor:
    """Ingests Reddit community discussions into the raw_reddit staging table using PRAW / official API."""

    source_name = "reddit"

    def __init__(self):
        self.client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
        self.user_agent = os.getenv(
            "REDDIT_USER_AGENT",
            "VantageNews/2.0.0 (by /u/vantagenews)",
        ).strip()

    def _get_praw_instance(self) -> Optional[praw.Reddit]:
        """Initialize PRAW instance if credentials are configured."""
        if self.client_id and self.client_secret:
            try:
                return praw.Reddit(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    user_agent=self.user_agent,
                )
            except Exception as e:
                logger.warning("[reddit] Failed initializing PRAW: %s", str(e))
        return None

    def fetch_and_stage(
        self,
        topic: Topic,
        db: Session,
        limit: int = 50,
        timeout_seconds: float = 10.0,
    ) -> List[RawReddit]:
        """Fetch posts for the topic and persist to raw_reddit staging table."""
        logger.info(
            "Fetching [reddit] posts for topic '%s' (ID %d, limit=%d, timeout=%.1fs)",
            topic.title,
            topic.id,
            limit,
            timeout_seconds,
        )

        from app.core.resilience import BackoffStrategy, JitterMode, reddit_breaker, retry_with_backoff

        if not reddit_breaker.allow_request():
            logger.warning("[reddit] Circuit breaker is OPEN. Short-circuiting request for topic '%s'", topic.title)
            return []

        reddit = self._get_praw_instance()
        if reddit:
            try:
                def _do_praw():
                    staged: List[RawReddit] = []
                    subreddit = reddit.subreddit("all")
                    for post in subreddit.search(query=topic.title, sort="relevance", limit=limit):
                        title = getattr(post, "title", "") or ""
                        selftext = getattr(post, "selftext", "") or ""
                        if selftext in ["[removed]", "[deleted]"]:
                            selftext = ""
                        body = f"{title}\n\n{selftext}".strip() if selftext else title

                        if not body:
                            continue

                        author_name = str(post.author.name) if getattr(post, "author", None) else None
                        created_utc = (
                            datetime.utcfromtimestamp(post.created_utc)
                            if hasattr(post, "created_utc")
                            else datetime.utcnow()
                        )

                        record = RawReddit(
                            slug_id=topic.id,
                            post_id=str(getattr(post, "id", "")),
                            body=body,
                            score=int(getattr(post, "score", 0)),
                            num_comments=int(getattr(post, "num_comments", 0)),
                            subreddit=str(getattr(post, "subreddit", "all")),
                            author=author_name,
                            created_utc=created_utc,
                        )
                        db.add(record)
                        staged.append(record)

                    db.commit()
                    return staged

                staged = reddit_breaker.execute(_do_praw)
                logger.info("[reddit] Staged %d records via PRAW for Topic ID %d", len(staged), topic.id)
                return staged
            except Exception as e:
                logger.error("[reddit] PRAW search error: %s - falling back to REST endpoint", str(e))

        # Fallback to public REST endpoint with custom User-Agent
        return self._fetch_via_public_rest(topic=topic, db=db, limit=limit, timeout_seconds=timeout_seconds)

    def _fetch_via_public_rest(
        self,
        topic: Topic,
        db: Session,
        limit: int = 50,
        timeout_seconds: float = 10.0,
    ) -> List[RawReddit]:
        """Fallback public REST API scraper."""
        from app.core.resilience import BackoffStrategy, JitterMode, reddit_breaker, retry_with_backoff

        if not reddit_breaker.allow_request():
            logger.warning("[reddit] Circuit breaker is OPEN. Short-circuiting REST request for topic '%s'", topic.title)
            return []

        params = urllib.parse.urlencode({
            "q": topic.title,
            "sort": "relevance",
            "limit": min(limit, 100),
            "raw_json": 1,
        })
        url = f"https://www.reddit.com/search.json?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})

        def _fetch_rest():
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                return resp.read().decode("utf-8")

        backoff = BackoffStrategy(base_delay=0.3, max_delay=3.0, multiplier=2.0, jitter_mode=JitterMode.FULL)
        fetch_with_retries = retry_with_backoff(
            max_attempts=2,
            backoff=backoff,
            retryable_exceptions=(urllib.error.URLError, TimeoutError, OSError),
            reraise_last=True,
        )(_fetch_rest)

        t_start = time.perf_counter()
        try:
            raw_json = reddit_breaker.execute(fetch_with_retries)
            latency_ms = (time.perf_counter() - t_start) * 1000.0
            ops_metrics.record_source_execution("reddit", success=True, latency_ms=latency_ms)
            return self.parse_json_and_persist(raw_json, topic_id=topic.id, db=db, limit=limit)
        except Exception as e:
            latency_ms = (time.perf_counter() - t_start) * 1000.0
            is_to = "timed out" in str(e).lower()
            ops_metrics.record_source_execution(
                "reddit",
                success=False,
                latency_ms=latency_ms,
                is_timeout=is_to,
                error_summary=str(e)[:100],
            )
            logger.error("[reddit] Public REST fallback error for '%s': %s", topic.title, str(e))
            return []

    def parse_json_and_persist(
        self,
        raw_json: str,
        topic_id: int,
        db: Session,
        limit: int = 50,
    ) -> List[RawReddit]:
        """Parse Reddit search JSON and persist to RawReddit staging table."""
        try:
            data = json.loads(raw_json)
        except Exception as e:
            logger.error("[reddit] JSON parse error: %s", str(e))
            return []

        children = data.get("data", {}).get("children", [])
        staged: List[RawReddit] = []

        for index, child in enumerate(children):
            if len(staged) >= limit:
                break
            try:
                post = child.get("data", {})
                title = (post.get("title") or "").strip()
                selftext = (post.get("selftext") or "").strip()
                if selftext in ["[removed]", "[deleted]"]:
                    selftext = ""
                body = f"{title}\n\n{selftext}".strip() if selftext else title

                if not body:
                    continue

                created_utc_ts = post.get("created_utc")
                created_utc = (
                    datetime.utcfromtimestamp(created_utc_ts)
                    if created_utc_ts
                    else datetime.utcnow()
                )

                record = RawReddit(
                    slug_id=topic_id,
                    post_id=str(post.get("id") or ""),
                    body=body,
                    score=int(post.get("score", 0)),
                    num_comments=int(post.get("num_comments", 0)),
                    subreddit=post.get("subreddit"),
                    author=post.get("author"),
                    created_utc=created_utc,
                )
                db.add(record)
                staged.append(record)
            except Exception as item_err:
                logger.warning("[reddit] Skipping malformed post at #%d: %s", index, str(item_err))
                continue

        db.commit()
        logger.info("[reddit] Staged %d records for Topic ID %d", len(staged), topic_id)
        return staged
