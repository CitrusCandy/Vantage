"""Production Cost Control, Rate Limiting & Resource Governance.

Provides lightweight, in-process mechanisms for:
- API rate limiting (sliding window token buckets)
- Resource budget enforcement
- Cost-control tracking (embedding/synthesis calls, tokens, items)
- Concurrency governance (pipelines, source calls, backups)
- External API request governance (per-source budgets, retries, backoff)
- Utilization monitoring with configurable warning thresholds

All classes are thread-safe and use bounded data structures.
No external dependencies (Redis, etc.) required.
"""

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("app.core.resource_governor")

# ==========================================
# Rate Limiter
# ==========================================

# Default rate limit configurations: (max_requests, window_seconds)
DEFAULT_RATE_LIMITS: Dict[str, Tuple[int, int]] = {
    # Public endpoints
    "public:topic_create": (10, 60),
    "public:ingestion": (10, 60),
    "public:merge": (15, 60),
    "public:clustering": (10, 60),
    "public:synthesis": (5, 60),
    "public:pipeline": (5, 60),
    # Operational endpoints
    "ops:trending": (5, 30),
    "ops:refresh_topic": (10, 30),
    "ops:reprocess_topic": (5, 30),
    "ops:backup_create": (3, 60),
    "ops:backup_verify": (5, 60),
    "ops:maintenance": (3, 60),
}


@dataclass
class _RateBucket:
    """Tracks request timestamps for a single rate limit key."""
    timestamps: List[float] = field(default_factory=list)
    last_access: float = 0.0


class InProcessRateLimiter:
    """Sliding-window rate limiter with bounded memory and periodic cleanup.

    Each key maintains a list of recent request timestamps. Expired entries
    are pruned on every check_rate_limit call and via periodic cleanup.
    """

    MAX_BUCKETS = 10000  # Hard cap on tracked keys to prevent unbounded growth
    CLEANUP_INTERVAL = 300.0  # Seconds between full cleanup sweeps

    def __init__(self, limits: Optional[Dict[str, Tuple[int, int]]] = None):
        self._lock = threading.Lock()
        self._buckets: Dict[str, _RateBucket] = {}
        self._last_cleanup = time.monotonic()
        self._limits = dict(DEFAULT_RATE_LIMITS)
        if limits:
            self._limits.update(limits)

        # Allow env-based overrides: RATE_LIMIT_<KEY>=max_requests,window_seconds
        for key in list(self._limits.keys()):
            env_key = "RATE_LIMIT_" + key.replace(":", "_").replace(".", "_").upper()
            env_val = os.getenv(env_key, "").strip()
            if env_val:
                try:
                    parts = env_val.split(",")
                    if len(parts) == 2:
                        self._limits[key] = (int(parts[0]), int(parts[1]))
                except (ValueError, IndexError):
                    logger.warning("Invalid rate limit env override %s=%s", env_key, env_val)

    def check_rate_limit(self, key: str) -> Tuple[bool, float]:
        """Check if a request is allowed for the given key.

        Returns:
            (allowed, retry_after): allowed=True if request is within limit,
            retry_after=seconds until next slot opens (0.0 if allowed).
        """
        now = time.monotonic()

        limit_config = self._limits.get(key)
        if not limit_config:
            return True, 0.0

        max_requests, window_seconds = limit_config

        with self._lock:
            # Periodic cleanup of all stale buckets
            if now - self._last_cleanup > self.CLEANUP_INTERVAL:
                self._cleanup_expired(now)
                self._last_cleanup = now

            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self.MAX_BUCKETS:
                    # Evict oldest bucket to prevent unbounded growth
                    oldest_key = min(self._buckets, key=lambda k: self._buckets[k].last_access)
                    del self._buckets[oldest_key]
                bucket = _RateBucket()
                self._buckets[key] = bucket

            # Prune timestamps outside the current window
            cutoff = now - window_seconds
            bucket.timestamps = [t for t in bucket.timestamps if t > cutoff]

            if len(bucket.timestamps) < max_requests:
                bucket.timestamps.append(now)
                bucket.last_access = now
                return True, 0.0

            # Calculate retry_after from the oldest timestamp in the window
            oldest_in_window = bucket.timestamps[0]
            retry_after = (oldest_in_window + window_seconds) - now
            retry_after = max(0.1, retry_after)  # Ensure minimum positive value
            return False, round(retry_after, 1)

    def get_usage(self) -> Dict[str, Any]:
        """Return current rate limit usage for all configured keys."""
        now = time.monotonic()
        result = {}
        with self._lock:
            for key, (max_requests, window_seconds) in self._limits.items():
                bucket = self._buckets.get(key)
                if bucket:
                    cutoff = now - window_seconds
                    current = len([t for t in bucket.timestamps if t > cutoff])
                else:
                    current = 0
                result[key] = {
                    "current": current,
                    "limit": max_requests,
                    "window_seconds": window_seconds,
                    "utilization_pct": round((current / max_requests) * 100, 1) if max_requests > 0 else 0.0,
                }
        return result

    def _cleanup_expired(self, now: float) -> int:
        """Remove all buckets that have had no activity within their window. Returns count removed."""
        removed = 0
        keys_to_remove = []
        for key, bucket in self._buckets.items():
            max_window = self._limits.get(key, (0, 60))[1]
            if now - bucket.last_access > max_window * 2:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            del self._buckets[key]
            removed += 1
        if removed:
            logger.debug("Rate limiter cleanup: removed %d expired buckets", removed)
        return removed

    def cleanup_expired_buckets(self) -> int:
        """Public method to trigger manual cleanup. Returns count of removed buckets."""
        now = time.monotonic()
        with self._lock:
            return self._cleanup_expired(now)


# ==========================================
# Resource Budget Manager
# ==========================================


class ResourceBudgetManager:
    """Configurable resource budgets for expensive operations.

    All limits are loaded from environment variables with sensible defaults.
    """

    def __init__(self):
        self.budgets: Dict[str, int] = {
            "max_ingestion_items_per_source": int(os.getenv("BUDGET_MAX_INGESTION_ITEMS_PER_SOURCE", "100")),
            "max_merged_items_per_topic": int(os.getenv("BUDGET_MAX_MERGED_ITEMS_PER_TOPIC", "5000")),
            "max_processing_items": int(os.getenv("BUDGET_MAX_PROCESSING_ITEMS", "5000")),
            "max_embedding_batch_size": int(os.getenv("BUDGET_MAX_EMBEDDING_BATCH_SIZE", "100")),
            "max_embeddings_per_run": int(os.getenv("BUDGET_MAX_EMBEDDINGS_PER_RUN", "5000")),
            "max_clusters_to_llm": int(os.getenv("BUDGET_MAX_CLUSTERS_TO_LLM", "10")),
            "max_samples_per_cluster": int(os.getenv("BUDGET_MAX_SAMPLES_PER_CLUSTER", "20")),
            "max_synthesis_per_topic_hour": int(os.getenv("BUDGET_MAX_SYNTHESIS_PER_TOPIC_HOUR", "5")),
            "max_concurrent_pipelines": int(os.getenv("BUDGET_MAX_CONCURRENT_PIPELINES", "3")),
            "max_concurrent_source_calls": int(os.getenv("BUDGET_MAX_CONCURRENT_SOURCE_CALLS", "5")),
            "max_backup_operations": int(os.getenv("BUDGET_MAX_BACKUP_OPERATIONS", "2")),
            "max_maintenance_operations": int(os.getenv("BUDGET_MAX_MAINTENANCE_OPERATIONS", "2")),
        }

    def check_budget(self, resource: str, current: int) -> Tuple[bool, int, int]:
        """Check if current usage is within budget.

        Returns:
            (allowed, limit, current): allowed is True if current < limit.
        """
        limit = self.budgets.get(resource)
        if limit is None:
            return True, 0, current
        return current < limit, limit, current

    def get_limit(self, resource: str) -> int:
        """Get the configured limit for a resource."""
        return self.budgets.get(resource, 0)

    def clamp(self, resource: str, value: int) -> int:
        """Clamp a value to the budget limit for a resource."""
        limit = self.budgets.get(resource, value)
        if value > limit:
            logger.info(
                "Clamping %s from %d to budget limit %d",
                resource, value, limit,
            )
        return min(value, limit)

    def get_all_budgets(self) -> Dict[str, int]:
        """Return all configured budgets (safe to expose, no secrets)."""
        return dict(self.budgets)


# ==========================================
# Cost Tracker
# ==========================================


class CostTracker:
    """Persistent operational metadata tracking for cost-control visibility.

    Tracks counts and estimated tokens only. Never stores prompt contents
    or raw source text.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {
            "embedding_calls": 0,
            "embedding_items_total": 0,
            "synthesis_calls": 0,
            "estimated_input_tokens": 0,
            "estimated_output_tokens": 0,
            "items_processed": 0,
            "pipeline_invocations": 0,
        }
        self._source_requests: Dict[str, int] = defaultdict(int)

    def record_embedding_call(self, item_count: int = 0):
        """Record an embedding API call."""
        with self._lock:
            self._counters["embedding_calls"] += 1
            self._counters["embedding_items_total"] += item_count

    def record_synthesis_call(
        self,
        estimated_input_tokens: int = 0,
        estimated_output_tokens: int = 0,
    ):
        """Record an LLM synthesis API call."""
        with self._lock:
            self._counters["synthesis_calls"] += 1
            self._counters["estimated_input_tokens"] += estimated_input_tokens
            self._counters["estimated_output_tokens"] += estimated_output_tokens

    def record_items_processed(self, count: int = 1):
        """Record items processed through pipelines."""
        with self._lock:
            self._counters["items_processed"] += count

    def record_pipeline_invocation(self):
        """Record a pipeline invocation."""
        with self._lock:
            self._counters["pipeline_invocations"] += 1

    def record_external_request(self, source: str):
        """Record an external API request for a source."""
        with self._lock:
            self._source_requests[source] += 1

    def get_usage(self) -> Dict[str, Any]:
        """Return all tracked counters for API exposure."""
        with self._lock:
            return {
                **dict(self._counters),
                "external_requests": dict(self._source_requests),
            }

    def reset(self):
        """Reset all counters (for testing)."""
        with self._lock:
            for key in self._counters:
                self._counters[key] = 0
            self._source_requests.clear()


# ==========================================
# Concurrency Governor
# ==========================================


class ConcurrencyGovernor:
    """Bounded concurrency control for expensive operations.

    Provides acquire/release semantics with guaranteed release on failure.
    """

    def __init__(self, budget_manager: Optional["ResourceBudgetManager"] = None):
        self._lock = threading.Lock()
        bm = budget_manager or ResourceBudgetManager()
        self._limits: Dict[str, int] = {
            "pipeline": bm.get_limit("max_concurrent_pipelines"),
            "source_call": bm.get_limit("max_concurrent_source_calls"),
            "backup": bm.get_limit("max_backup_operations"),
            "maintenance": bm.get_limit("max_maintenance_operations"),
        }
        self._current: Dict[str, int] = {k: 0 for k in self._limits}
        # Track which identifiers hold each resource to prevent duplicates
        self._holders: Dict[str, set] = {k: set() for k in self._limits}

    def acquire(self, resource: str, holder_id: Optional[str] = None) -> bool:
        """Try to acquire a concurrency slot. Returns True if successful."""
        with self._lock:
            limit = self._limits.get(resource, 0)
            if limit <= 0:
                return True  # No limit configured

            # Check for duplicate holder
            if holder_id and holder_id in self._holders.get(resource, set()):
                logger.warning(
                    "Duplicate concurrency acquire for %s by %s",
                    resource, holder_id,
                )
                return False

            current = self._current.get(resource, 0)
            if current >= limit:
                logger.warning(
                    "Concurrency limit reached for %s: %d/%d",
                    resource, current, limit,
                )
                return False

            self._current[resource] = current + 1
            if holder_id:
                self._holders[resource].add(holder_id)
            return True

    def release(self, resource: str, holder_id: Optional[str] = None):
        """Release a concurrency slot."""
        with self._lock:
            current = self._current.get(resource, 0)
            if current > 0:
                self._current[resource] = current - 1
            if holder_id and resource in self._holders:
                self._holders[resource].discard(holder_id)

    def get_usage(self) -> Dict[str, Any]:
        """Return current concurrency usage for all resources."""
        with self._lock:
            return {
                resource: {
                    "current": self._current.get(resource, 0),
                    "limit": limit,
                    "utilization_pct": round(
                        (self._current.get(resource, 0) / limit) * 100, 1
                    ) if limit > 0 else 0.0,
                }
                for resource, limit in self._limits.items()
            }

    @contextmanager
    def slot(self, resource: str, holder_id: Optional[str] = None):
        """Context manager that acquires and guarantees release of a slot.

        Raises RuntimeError if the slot cannot be acquired.
        """
        if not self.acquire(resource, holder_id=holder_id):
            raise RuntimeError(
                f"Concurrency limit reached for {resource}"
            )
        try:
            yield
        finally:
            self.release(resource, holder_id=holder_id)

    def release_all(self):
        """Release all held slots (for shutdown cleanup)."""
        with self._lock:
            for resource in self._current:
                self._current[resource] = 0
            for resource in self._holders:
                self._holders[resource].clear()
        logger.info("Concurrency governor: all slots released (shutdown)")


# ==========================================
# External Request Governor
# ==========================================


@dataclass
class _ExternalSourceConfig:
    """Configuration for a single external API source."""
    max_concurrent: int = 3
    timeout_seconds: float = 15.0
    max_retries: int = 3
    backoff_base: float = 1.0
    hourly_budget: int = 200


class ExternalRequestGovernor:
    """Governs external API requests with per-source budgets, timeouts, and retry limits.

    Prevents unbounded retries and respects provider rate limits.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._configs: Dict[str, _ExternalSourceConfig] = {
            "google_news": _ExternalSourceConfig(
                max_concurrent=int(os.getenv("EXT_GOOGLE_NEWS_MAX_CONCURRENT", "3")),
                timeout_seconds=float(os.getenv("EXT_GOOGLE_NEWS_TIMEOUT", "15.0")),
                max_retries=int(os.getenv("EXT_GOOGLE_NEWS_MAX_RETRIES", "3")),
                backoff_base=float(os.getenv("EXT_GOOGLE_NEWS_BACKOFF", "1.0")),
                hourly_budget=int(os.getenv("EXT_GOOGLE_NEWS_HOURLY_BUDGET", "200")),
            ),
            "reddit": _ExternalSourceConfig(
                max_concurrent=int(os.getenv("EXT_REDDIT_MAX_CONCURRENT", "3")),
                timeout_seconds=float(os.getenv("EXT_REDDIT_TIMEOUT", "15.0")),
                max_retries=int(os.getenv("EXT_REDDIT_MAX_RETRIES", "3")),
                backoff_base=float(os.getenv("EXT_REDDIT_BACKOFF", "1.0")),
                hourly_budget=int(os.getenv("EXT_REDDIT_HOURLY_BUDGET", "200")),
            ),
            "x": _ExternalSourceConfig(
                max_concurrent=int(os.getenv("EXT_X_MAX_CONCURRENT", "3")),
                timeout_seconds=float(os.getenv("EXT_X_TIMEOUT", "15.0")),
                max_retries=int(os.getenv("EXT_X_MAX_RETRIES", "3")),
                backoff_base=float(os.getenv("EXT_X_BACKOFF", "1.0")),
                hourly_budget=int(os.getenv("EXT_X_HOURLY_BUDGET", "200")),
            ),
            "openai": _ExternalSourceConfig(
                max_concurrent=int(os.getenv("EXT_OPENAI_MAX_CONCURRENT", "5")),
                timeout_seconds=float(os.getenv("EXT_OPENAI_TIMEOUT", "30.0")),
                max_retries=int(os.getenv("EXT_OPENAI_MAX_RETRIES", "2")),
                backoff_base=float(os.getenv("EXT_OPENAI_BACKOFF", "2.0")),
                hourly_budget=int(os.getenv("EXT_OPENAI_HOURLY_BUDGET", "500")),
            ),
        }
        self._hourly_counts: Dict[str, List[float]] = defaultdict(list)
        self._active_counts: Dict[str, int] = defaultdict(int)

    def check_request_allowed(self, source: str) -> Tuple[bool, str]:
        """Check if an external request to the given source is allowed.

        Returns:
            (allowed, reason): reason is empty string if allowed.
        """
        config = self._configs.get(source)
        if not config:
            return True, ""

        now = time.monotonic()

        with self._lock:
            # Check concurrency
            if self._active_counts[source] >= config.max_concurrent:
                return False, f"Concurrent request limit ({config.max_concurrent}) reached for {source}"

            # Check hourly budget
            one_hour_ago = now - 3600.0
            self._hourly_counts[source] = [
                t for t in self._hourly_counts[source] if t > one_hour_ago
            ]
            if len(self._hourly_counts[source]) >= config.hourly_budget:
                return False, f"Hourly request budget ({config.hourly_budget}) exhausted for {source}"

        return True, ""

    def record_request_start(self, source: str):
        """Record that a request to the source has started."""
        with self._lock:
            self._active_counts[source] += 1
            self._hourly_counts[source].append(time.monotonic())

    def record_request_end(self, source: str):
        """Record that a request to the source has completed."""
        with self._lock:
            if self._active_counts[source] > 0:
                self._active_counts[source] -= 1

    def get_source_config(self, source: str) -> Optional[_ExternalSourceConfig]:
        """Get the configuration for a source."""
        return self._configs.get(source)

    def get_usage(self) -> Dict[str, Any]:
        """Return current external request usage for all sources."""
        now = time.monotonic()
        result = {}
        with self._lock:
            for source, config in self._configs.items():
                one_hour_ago = now - 3600.0
                hourly_used = len([t for t in self._hourly_counts.get(source, []) if t > one_hour_ago])
                result[source] = {
                    "active_requests": self._active_counts.get(source, 0),
                    "max_concurrent": config.max_concurrent,
                    "hourly_used": hourly_used,
                    "hourly_budget": config.hourly_budget,
                    "timeout_seconds": config.timeout_seconds,
                    "max_retries": config.max_retries,
                    "utilization_pct": round(
                        (hourly_used / config.hourly_budget) * 100, 1
                    ) if config.hourly_budget > 0 else 0.0,
                }
        return result


# ==========================================
# Utilization Monitor
# ==========================================


class UtilizationMonitor:
    """Monitors resource utilization against configurable warning thresholds."""

    def __init__(self):
        self.warning_threshold = float(os.getenv("RESOURCE_WARNING_THRESHOLD", "0.70"))
        self.critical_threshold = float(os.getenv("RESOURCE_CRITICAL_THRESHOLD", "0.90"))

    def get_status(self, current: float, limit: float) -> str:
        """Classify utilization as normal, warning, or critical.

        Args:
            current: Current usage value.
            limit: Maximum allowed value.

        Returns:
            'normal', 'warning', or 'critical'
        """
        if limit <= 0:
            return "normal"
        ratio = current / limit
        if ratio >= self.critical_threshold:
            return "critical"
        if ratio >= self.warning_threshold:
            return "warning"
        return "normal"

    def get_thresholds(self) -> Dict[str, float]:
        """Return configured thresholds (safe to expose)."""
        return {
            "warning_threshold": self.warning_threshold,
            "critical_threshold": self.critical_threshold,
        }


# ==========================================
# Synthesis Rate Tracker (per topic/hour)
# ==========================================


class SynthesisRateTracker:
    """Tracks synthesis calls per topic per hour to enforce budget limits."""

    def __init__(self, max_per_topic_hour: int = 5):
        self._lock = threading.Lock()
        self.max_per_topic_hour = int(
            os.getenv("BUDGET_MAX_SYNTHESIS_PER_TOPIC_HOUR", str(max_per_topic_hour))
        )
        self._topic_timestamps: Dict[str, List[float]] = {}

    def check_and_record(self, topic_slug: str) -> Tuple[bool, int, int]:
        """Check if synthesis is allowed for the topic, and record if so.

        Returns:
            (allowed, current_count, limit)
        """
        now = time.monotonic()
        one_hour_ago = now - 3600.0

        with self._lock:
            timestamps = self._topic_timestamps.get(topic_slug, [])
            # Prune expired
            timestamps = [t for t in timestamps if t > one_hour_ago]
            current = len(timestamps)

            if current >= self.max_per_topic_hour:
                self._topic_timestamps[topic_slug] = timestamps
                return False, current, self.max_per_topic_hour

            timestamps.append(now)
            self._topic_timestamps[topic_slug] = timestamps
            return True, current + 1, self.max_per_topic_hour

    def get_usage(self, topic_slug: Optional[str] = None) -> Dict[str, Any]:
        """Return synthesis rate usage."""
        now = time.monotonic()
        one_hour_ago = now - 3600.0
        with self._lock:
            if topic_slug:
                timestamps = self._topic_timestamps.get(topic_slug, [])
                current = len([t for t in timestamps if t > one_hour_ago])
                return {
                    "topic_slug": topic_slug,
                    "current_hour": current,
                    "limit": self.max_per_topic_hour,
                }
            # All topics
            result = {}
            for slug, timestamps in self._topic_timestamps.items():
                current = len([t for t in timestamps if t > one_hour_ago])
                if current > 0:
                    result[slug] = {
                        "current_hour": current,
                        "limit": self.max_per_topic_hour,
                    }
            return result


# ==========================================
# Global Singleton Instances
# ==========================================

rate_limiter = InProcessRateLimiter()
budget_manager = ResourceBudgetManager()
cost_tracker = CostTracker()
concurrency_governor = ConcurrencyGovernor(budget_manager)
external_governor = ExternalRequestGovernor()
utilization_monitor = UtilizationMonitor()
synthesis_tracker = SynthesisRateTracker()
