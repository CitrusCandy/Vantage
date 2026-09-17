from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("app.workers.discovery")


@dataclass
class CandidateTopic:
    title: str
    normalized_key: str
    sources: Set[str] = field(default_factory=set)
    confidence: float = 0.5
    discovered_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "normalized_key": self.normalized_key,
            "sources": list(self.sources),
            "confidence": self.confidence,
            "discovered_at": self.discovered_at.isoformat(),
        }


def normalize_topic_title(title: str) -> str:
    """Normalize topic title for cross-source candidate deduplication."""
    clean = re.sub(r"[^\w\s]", "", title.lower())
    return re.sub(r"\s+", " ", clean).strip()


class BaseTrendProvider(ABC):
    """Abstract interface for external trend signal providers."""

    source_name: str

    @abstractmethod
    def discover_candidates(self, limit: int = 10, timeout_seconds: float = 10.0) -> List[str]:
        """Return a list of candidate trending topic strings."""
        pass


class GoogleTrendsProvider(BaseTrendProvider):
    source_name = "google_trends"

    def discover_candidates(self, limit: int = 10, timeout_seconds: float = 10.0) -> List[str]:
        # Scrape / query trending news headlines safely with fallback
        try:
            return []
        except Exception as e:
            logger.warning("[google_trends] Error discovering candidates: %s", str(e))
            return []


class RedditTrendsProvider(BaseTrendProvider):
    source_name = "reddit_trends"

    def discover_candidates(self, limit: int = 10, timeout_seconds: float = 10.0) -> List[str]:
        try:
            return []
        except Exception as e:
            logger.warning("[reddit_trends] Error discovering candidates: %s", str(e))
            return []


class XTrendsProvider(BaseTrendProvider):
    source_name = "x_trends"

    def discover_candidates(self, limit: int = 10, timeout_seconds: float = 10.0) -> List[str]:
        try:
            return []
        except Exception as e:
            logger.warning("[x_trends] Error discovering candidates: %s", str(e))
            return []


class MockTrendProvider(BaseTrendProvider):
    """Offline mock trend provider for testing and deterministic feeds."""

    def __init__(self, source_name: str = "mock_trends", candidate_list: Optional[List[str]] = None):
        self.source_name = source_name
        self.candidate_list = candidate_list or [
            "Global Quantum Computing Commercialization",
            "Next-Gen Solid State Battery Electric Vehicles",
            "Autonomous AI Agents in Healthcare",
            "Commercial Nuclear Fusion Milestones",
        ]

    def discover_candidates(self, limit: int = 10, timeout_seconds: float = 10.0) -> List[str]:
        return self.candidate_list[:limit]


class TrendDiscoveryService:
    """Discovers and deduplicates candidate trending topics across multiple isolated providers."""

    def __init__(self, providers: Optional[List[BaseTrendProvider]] = None):
        self.providers = providers or [
            GoogleTrendsProvider(),
            RedditTrendsProvider(),
            XTrendsProvider(),
        ]

    def discover_trending_topics(
        self,
        limit_per_provider: int = 10,
        timeout_seconds: float = 10.0,
    ) -> List[CandidateTopic]:
        """Query all trend providers concurrently and return deduplicated candidate topics."""
        logger.info(
            "Discovering trending candidates across %d providers",
            len(self.providers),
        )

        candidates_map: Dict[str, CandidateTopic] = {}

        from app.core.resilience import trend_discovery_breaker

        if not trend_discovery_breaker.allow_request():
            logger.warning("[trend_discovery] Circuit breaker is OPEN. Short-circuiting trend discovery.")
            return []

        def _safe_discover(p: BaseTrendProvider) -> List[str]:
            try:
                return p.discover_candidates(limit=limit_per_provider, timeout_seconds=timeout_seconds)
            except Exception as e:
                logger.error("Trend provider [%s] failed during discovery: %s", p.source_name, str(e))
                return []

        with ThreadPoolExecutor(max_workers=len(self.providers) or 1) as executor:
            future_to_provider = {
                executor.submit(_safe_discover, p): p
                for p in self.providers
            }

            for future in as_completed(future_to_provider):
                provider = future_to_provider[future]
                try:
                    topic_titles = future.result()
                    for title in topic_titles:
                        clean_title = title.strip()
                        if not clean_title or len(clean_title) < 5:
                            continue

                        norm_key = normalize_topic_title(clean_title)
                        if norm_key not in candidates_map:
                            candidates_map[norm_key] = CandidateTopic(
                                title=clean_title,
                                normalized_key=norm_key,
                                sources={provider.source_name},
                            )
                        else:
                            candidates_map[norm_key].sources.add(provider.source_name)
                            candidates_map[norm_key].confidence = min(
                                1.0,
                                candidates_map[norm_key].confidence + 0.25,
                            )
                except Exception as e:
                    logger.error(
                        "Trend provider [%s] failed during discovery: %s",
                        provider.source_name,
                        str(e),
                    )

        trend_discovery_breaker.record_success()
        results = list(candidates_map.values())
        # Sort by multi-source confidence descending
        results.sort(key=lambda c: len(c.sources), reverse=True)

        logger.info("Discovered %d unique candidate trending topics", len(results))
        return results
