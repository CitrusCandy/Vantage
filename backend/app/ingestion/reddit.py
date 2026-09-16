import base64
from datetime import datetime
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from app.ingestion.base import BaseIngestor
from app.ingestion.schemas import IngestedItem

logger = logging.getLogger("app.ingestion.reddit")


class RedditIngestor(BaseIngestor):
    """Ingests and normalizes community posts from Reddit using Reddit API or OAuth."""

    source_name = "reddit"
    OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
    OAUTH_SEARCH_URL = "https://oauth.reddit.com/search.json"
    PUBLIC_SEARCH_URL = "https://www.reddit.com/search.json"

    def __init__(self):
        self.client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
        self.user_agent = os.getenv(
            "REDDIT_USER_AGENT",
            "VantageNews/1.0.0 (by /u/vantagenews)",
        ).strip()
        self._access_token: Optional[str] = None

    def _get_oauth_token(self, timeout_seconds: float = 5.0) -> Optional[str]:
        """Obtain application-only OAuth2 bearer token from Reddit API."""
        if not self.client_id or not self.client_secret:
            return None

        auth_header = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("utf-8")

        data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8")
        req = urllib.request.Request(
            self.OAUTH_TOKEN_URL,
            data=data,
            headers={
                "Authorization": f"Basic {auth_header}",
                "User-Agent": self.user_agent,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload.get("access_token")
        except Exception as e:
            logger.warning("[reddit] OAuth token request failed, falling back to public endpoint: %s", str(e))
            return None

    def fetch_items(
        self,
        query: str,
        limit: int = 25,
        timeout_seconds: float = 10.0,
    ) -> List[IngestedItem]:
        logger.info(
            "Source requested: [reddit] for query '%s' (limit=%d, timeout=%.1fs)",
            query,
            limit,
            timeout_seconds,
        )

        params = urllib.parse.urlencode({
            "q": query,
            "sort": "relevance",
            "limit": min(limit, 100),
            "raw_json": 1,
        })

        token = self._get_oauth_token(timeout_seconds=min(5.0, timeout_seconds))
        if token:
            url = f"{self.OAUTH_SEARCH_URL}?{params}"
            headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": self.user_agent,
            }
        else:
            url = f"{self.PUBLIC_SEARCH_URL}?{params}"
            headers = {
                "User-Agent": self.user_agent,
            }

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                raw_json = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                logger.error("Source failure: [reddit] rate limited (HTTP 429) for query '%s'", query)
            else:
                logger.error("Source failure: [reddit] HTTP %d error for query '%s': %s", e.code, query, str(e))
            return []
        except urllib.error.URLError as e:
            logger.error("Source failure/timeout: [reddit] connection error for '%s': %s", query, str(e))
            return []
        except Exception as e:
            logger.error("Source failure: [reddit] unexpected error for '%s': %s", query, str(e))
            return []

        return self.parse_json_response(raw_json, limit=limit)

    def parse_json_response(self, raw_json: str, limit: int = 25) -> List[IngestedItem]:
        """Parse Reddit API JSON response and normalize items into IngestedItem."""
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            logger.error("Source failure: [reddit] JSON decode error: %s", str(e))
            return []

        children = data.get("data", {}).get("children", [])
        logger.info("Number of results received: [reddit] found %d posts", len(children))

        normalized_items: List[IngestedItem] = []
        for index, child in enumerate(children):
            if len(normalized_items) >= limit:
                break
            try:
                post = child.get("data", {})
                title = (post.get("title") or "").strip()
                selftext = (post.get("selftext") or "").strip()

                if selftext in ["[removed]", "[deleted]"]:
                    selftext = ""

                text_parts = []
                if title:
                    text_parts.append(title)
                if selftext:
                    text_parts.append(selftext)
                text_content = "\n\n".join(text_parts).strip()

                if not text_content:
                    continue

                permalink = post.get("permalink", "")
                url = f"https://www.reddit.com{permalink}" if permalink else post.get("url")
                author = post.get("author")
                author_handle = f"u/{author}" if author else "u/[unknown]"

                created_utc = post.get("created_utc")
                if created_utc:
                    created_at = datetime.utcfromtimestamp(created_utc)
                else:
                    created_at = datetime.utcnow()

                metrics = {
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                    "upvote_ratio": post.get("upvote_ratio", 1.0),
                    "subreddit": post.get("subreddit"),
                }

                item = IngestedItem(
                    source=self.source_name,
                    text_content=text_content,
                    url=url,
                    author_handle=author_handle,
                    engagement_metrics=metrics,
                    created_at=created_at,
                )
                normalized_items.append(item)
            except Exception as item_err:
                logger.warning(
                    "[reddit] Malformed post entry skipped at index #%d: %s",
                    index,
                    str(item_err),
                )
                continue

        logger.info(
            "Number successfully normalized: [reddit] %d / %d posts",
            len(normalized_items),
            len(children),
        )
        return normalized_items
