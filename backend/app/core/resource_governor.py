"""Production Cost Control, Rate Limiting, Resource Governance & Distributed Multi-Worker Coordination.

Provides robust, pluggable governance mechanisms for:
- API rate limiting (distributed sliding window token buckets via Redis / in-process fallback)
- Concurrency governance (distributed leasing with TTL and deadlock prevention)
- Resource budget enforcement & clamp guards
- Cost-control tracking (distributed counters for embedding/synthesis calls, tokens, items)
- External API request governance (per-source budgets, retries, backoff)
- Utilization monitoring with configurable warning thresholds
- Automatic zero-downtime graceful fallback to in-memory coordination on shared store disruption

Thread-safe, bounded memory, zero hardcoded secrets.
"""

from abc import ABC, abstractmethod
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

logger = logging.getLogger("app.core.resource_governor")

# ==========================================
# Default Configurations
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


# ==========================================
# Storage Abstraction Interface
# ==========================================


class BaseGovernanceStore(ABC):
    """Abstract interface for governance storage backends (In-Memory, Redis, etc.)."""

    @abstractmethod
    def check_rate_limit(
        self, key: str, max_requests: int, window_seconds: int
    ) -> Tuple[bool, float, int]:
        """Check if request is permitted. Returns (allowed, retry_after, current_count)."""
        pass

    @abstractmethod
    def get_rate_limit_usage(
        self, limits: Dict[str, Tuple[int, int]]
    ) -> Dict[str, Any]:
        """Get current rate limit usage for configured limits."""
        pass

    @abstractmethod
    def acquire_concurrency(
        self, resource: str, limit: int, holder_id: Optional[str] = None, ttl_seconds: int = 120
    ) -> bool:
        """Attempt to acquire concurrency slot. Returns True if acquired."""
        pass

    @abstractmethod
    def release_concurrency(
        self, resource: str, holder_id: Optional[str] = None
    ) -> None:
        """Release held concurrency slot."""
        pass

    @abstractmethod
    def get_concurrency_usage(
        self, limits: Dict[str, int]
    ) -> Dict[str, Any]:
        """Get current concurrency usage."""
        pass

    @abstractmethod
    def release_all_concurrency(self) -> None:
        """Release all held concurrency slots (for shutdown)."""
        pass

    @abstractmethod
    def record_cost_counter(self, counter_name: str, delta: int = 1) -> None:
        """Increment cost counter."""
        pass

    @abstractmethod
    def record_external_request(self, source: str, delta: int = 1) -> None:
        """Increment external request count for source."""
        pass

    @abstractmethod
    def get_cost_usage(self) -> Dict[str, Any]:
        """Get tracked cost counters and external requests."""
        pass

    @abstractmethod
    def reset_cost_usage(self) -> None:
        """Reset cost counters (for testing)."""
        pass

    @abstractmethod
    def check_synthesis_rate(
        self, topic_slug: str, limit: int, window_seconds: int = 3600
    ) -> Tuple[bool, int, int]:
        """Check and record synthesis rate. Returns (allowed, current_count, limit)."""
        pass

    @abstractmethod
    def get_synthesis_usage(
        self, limit: int, topic_slug: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get synthesis rate usage."""
        pass

    @abstractmethod
    def is_healthy(self) -> bool:
        """Health check for the store."""
        pass

    @abstractmethod
    def get_backend_name(self) -> str:
        """Return backend identifier name."""
        pass


# ==========================================
# In-Process In-Memory Governance Store
# ==========================================


@dataclass
class _RateBucket:
    """Tracks request timestamps for a single in-process rate limit key."""
    timestamps: List[float] = field(default_factory=list)
    last_access: float = 0.0


class InMemoryGovernanceStore(BaseGovernanceStore):
    """Thread-safe in-process governance store with bounded LRU memory management."""

    MAX_BUCKETS = 10000
    CLEANUP_INTERVAL = 300.0

    def __init__(self):
        self._lock = threading.Lock()
        self._buckets: Dict[str, _RateBucket] = {}
        self._last_cleanup = time.monotonic()

        # Concurrency state
        self._concurrency_counts: Dict[str, int] = defaultdict(int)
        self._concurrency_holders: Dict[str, Set[str]] = defaultdict(set)
        self._holder_timestamps: Dict[Tuple[str, str], float] = {}

        # Cost tracking state
        self._cost_counters: Dict[str, int] = {
            "embedding_calls": 0,
            "embedding_items_total": 0,
            "synthesis_calls": 0,
            "estimated_input_tokens": 0,
            "estimated_output_tokens": 0,
            "items_processed": 0,
            "pipeline_invocations": 0,
        }
        self._source_requests: Dict[str, int] = defaultdict(int)

        # Synthesis rate tracking state
        self._synthesis_timestamps: Dict[str, List[float]] = defaultdict(list)

    def cleanup_expired_buckets(self, max_idle_seconds: float = 300.0) -> int:
        """Explicitly purge expired rate limit buckets."""
        now = time.monotonic()
        with self._lock:
            expired_keys = [
                k for k, b in self._buckets.items()
                if now - b.last_access > max_idle_seconds
            ]
            for k in expired_keys:
                del self._buckets[k]
            return len(expired_keys)

    def _cleanup_expired(self, now: float):
        """Evict stale rate buckets and expired concurrency leases."""
        expired_keys = [
            k for k, b in self._buckets.items()
            if now - b.last_access > 3600.0
        ]
        for k in expired_keys:
            del self._buckets[k]

    def check_rate_limit(
        self, key: str, max_requests: int, window_seconds: int
    ) -> Tuple[bool, float, int]:
        now = time.monotonic()
        with self._lock:
            if now - self._last_cleanup > self.CLEANUP_INTERVAL:
                self._cleanup_expired(now)
                self._last_cleanup = now

            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self.MAX_BUCKETS:
                    oldest_key = min(self._buckets, key=lambda k: self._buckets[k].last_access)
                    del self._buckets[oldest_key]
                bucket = _RateBucket()
                self._buckets[key] = bucket

            cutoff = now - window_seconds
            bucket.timestamps = [t for t in bucket.timestamps if t > cutoff]

            if len(bucket.timestamps) < max_requests:
                bucket.timestamps.append(now)
                bucket.last_access = now
                return True, 0.0, len(bucket.timestamps)

            oldest_in_window = bucket.timestamps[0]
            retry_after = (oldest_in_window + window_seconds) - now
            retry_after = max(0.1, retry_after)
            return False, round(retry_after, 1), len(bucket.timestamps)

    def get_rate_limit_usage(
        self, limits: Dict[str, Tuple[int, int]]
    ) -> Dict[str, Any]:
        now = time.monotonic()
        result = {}
        with self._lock:
            for key, (max_requests, window_seconds) in limits.items():
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

    def acquire_concurrency(
        self, resource: str, limit: int, holder_id: Optional[str] = None, ttl_seconds: int = 120
    ) -> bool:
        now = time.monotonic()
        with self._lock:
            # Prune expired holder leases for this resource
            if resource in self._concurrency_holders:
                expired_holders = [
                    h for h in list(self._concurrency_holders[resource])
                    if now - self._holder_timestamps.get((resource, h), 0) > ttl_seconds
                ]
                for h in expired_holders:
                    self._concurrency_holders[resource].discard(h)
                    self._holder_timestamps.pop((resource, h), None)
                self._concurrency_counts[resource] = len(self._concurrency_holders[resource])

            if limit <= 0:
                return True

            if holder_id and holder_id in self._concurrency_holders[resource]:
                logger.warning("Duplicate concurrency acquire for %s by %s", resource, holder_id)
                return False

            current = self._concurrency_counts[resource]
            if current >= limit:
                return False

            self._concurrency_counts[resource] = current + 1
            if holder_id:
                self._concurrency_holders[resource].add(holder_id)
                self._holder_timestamps[(resource, holder_id)] = now
            return True

    def release_concurrency(
        self, resource: str, holder_id: Optional[str] = None
    ) -> None:
        now = time.monotonic()
        with self._lock:
            if holder_id and resource in self._concurrency_holders:
                if holder_id in self._concurrency_holders[resource]:
                    self._concurrency_holders[resource].discard(holder_id)
                    self._holder_timestamps.pop((resource, holder_id), None)
                    self._concurrency_counts[resource] = len(self._concurrency_holders[resource])
            else:
                current = self._concurrency_counts.get(resource, 0)
                if current > 0:
                    self._concurrency_counts[resource] = current - 1

    def get_concurrency_usage(
        self, limits: Dict[str, int]
    ) -> Dict[str, Any]:
        with self._lock:
            return {
                resource: {
                    "current": self._concurrency_counts.get(resource, 0),
                    "limit": limit,
                    "utilization_pct": round(
                        (self._concurrency_counts.get(resource, 0) / limit) * 100, 1
                    ) if limit > 0 else 0.0,
                }
                for resource, limit in limits.items()
            }

    def release_all_concurrency(self) -> None:
        with self._lock:
            self._concurrency_counts.clear()
            self._concurrency_holders.clear()
            self._holder_timestamps.clear()

    def record_cost_counter(self, counter_name: str, delta: int = 1) -> None:
        with self._lock:
            if counter_name in self._cost_counters:
                self._cost_counters[counter_name] += delta

    def record_external_request(self, source: str, delta: int = 1) -> None:
        with self._lock:
            self._source_requests[source] += delta

    def get_cost_usage(self) -> Dict[str, Any]:
        with self._lock:
            return {
                **dict(self._cost_counters),
                "external_requests": dict(self._source_requests),
            }

    def reset_cost_usage(self) -> None:
        with self._lock:
            for k in self._cost_counters:
                self._cost_counters[k] = 0
            self._source_requests.clear()

    def check_synthesis_rate(
        self, topic_slug: str, limit: int, window_seconds: int = 3600
    ) -> Tuple[bool, int, int]:
        now = time.monotonic()
        cutoff = now - float(window_seconds)
        with self._lock:
            timestamps = self._synthesis_timestamps[topic_slug]
            timestamps = [t for t in timestamps if t > cutoff]

            if len(timestamps) >= limit:
                self._synthesis_timestamps[topic_slug] = timestamps
                return False, len(timestamps), limit

            timestamps.append(now)
            self._synthesis_timestamps[topic_slug] = timestamps
            return True, len(timestamps), limit

    def get_synthesis_usage(
        self, limit: int, topic_slug: Optional[str] = None
    ) -> Dict[str, Any]:
        now = time.monotonic()
        cutoff = now - 3600.0
        with self._lock:
            if topic_slug:
                timestamps = self._synthesis_timestamps.get(topic_slug, [])
                current = len([t for t in timestamps if t > cutoff])
                return {
                    "topic_slug": topic_slug,
                    "current_hour": current,
                    "limit": limit,
                }
            result = {}
            for slug, timestamps in self._synthesis_timestamps.items():
                current = len([t for t in timestamps if t > cutoff])
                if current > 0:
                    result[slug] = {
                        "current_hour": current,
                        "limit": limit,
                    }
            return result

    def is_healthy(self) -> bool:
        return True

    def get_backend_name(self) -> str:
        return "memory"


# ==========================================
# Redis Distributed Governance Store
# ==========================================

# Atomic sliding-window rate limit Lua script
# Returns [allowed (0/1), retry_after (float), current_count (int)]
REDIS_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local clearBefore = now - window

redis.call('ZREMRANGEBYSCORE', key, '-inf', clearBefore)
local currentCount = redis.call('ZCARD', key)

if currentCount < limit then
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, math.ceil(window) + 15)
    return {1, 0.0, currentCount + 1}
else
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retryAfter = 0.1
    if oldest and #oldest >= 2 then
        local oldestScore = tonumber(oldest[2])
        retryAfter = (oldestScore + window) - now
        if retryAfter < 0.1 then retryAfter = 0.1 end
    end
    return {0, retryAfter, currentCount}
end
"""

# Atomic concurrency acquire Lua script
# Returns 1 if acquired, 0 if rejected
REDIS_CONCURRENCY_ACQUIRE_LUA = """
local holdersKey = KEYS[1]
local leaseKey = KEYS[2]
local holder = ARGV[1]
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

-- Check if holder already acquired
if redis.call('SISMEMBER', holdersKey, holder) == 1 then
    return 0
end

-- Prune expired member leases
local allHolders = redis.call('SMEMBERS', holdersKey)
for _, h in ipairs(allHolders) do
    local lk = holdersKey .. ':lease:' .. h
    if redis.call('EXISTS', lk) == 0 then
        redis.call('SREM', holdersKey, h)
    end
end

local current = redis.call('SCARD', holdersKey)
if limit > 0 and current >= limit then
    return 0
end

redis.call('SADD', holdersKey, holder)
redis.call('SET', leaseKey, 1, 'EX', ttl)
return 1
"""

# Atomic concurrency release Lua script
REDIS_CONCURRENCY_RELEASE_LUA = """
local holdersKey = KEYS[1]
local leaseKey = KEYS[2]
local holder = ARGV[1]

redis.call('SREM', holdersKey, holder)
redis.call('DEL', leaseKey)
return 1
"""


class RedisGovernanceStore(BaseGovernanceStore):
    """Distributed governance store backed by Redis with atomic Lua scripting and TTLs."""

    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "vantage:gov:",
    ):
        self.client = redis_client
        self.prefix = key_prefix
        self._rate_script = None
        self._acquire_script = None
        self._release_script = None
        self._init_scripts()

    def _init_scripts(self):
        """Register Lua scripts with Redis for high-performance atomic execution."""
        try:
            self._rate_script = self.client.register_script(REDIS_SLIDING_WINDOW_LUA)
            self._acquire_script = self.client.register_script(REDIS_CONCURRENCY_ACQUIRE_LUA)
            self._release_script = self.client.register_script(REDIS_CONCURRENCY_RELEASE_LUA)
        except Exception as e:
            logger.debug("Script pre-registration note: %s", str(e))

    def _k(self, name: str) -> str:
        return f"{self.prefix}{name}"

    def check_rate_limit(
        self, key: str, max_requests: int, window_seconds: int
    ) -> Tuple[bool, float, int]:
        r_key = self._k(f"ratelimit:{key}")
        now = time.time()
        member_id = f"{now}:{uuid.uuid4().hex[:8]}"

        if self._rate_script is not None:
            res = self._rate_script(
                keys=[r_key],
                args=[str(now), str(window_seconds), str(max_requests), member_id],
            )
        else:
            res = self.client.eval(
                REDIS_SLIDING_WINDOW_LUA,
                1,
                r_key,
                str(now),
                str(window_seconds),
                str(max_requests),
                member_id,
            )

        allowed = bool(res[0] == 1)
        retry_after = round(float(res[1]), 1)
        current = int(res[2])
        return allowed, retry_after, current

    def get_rate_limit_usage(
        self, limits: Dict[str, Tuple[int, int]]
    ) -> Dict[str, Any]:
        now = time.time()
        result = {}
        pipeline = self.client.pipeline()
        for key, (max_requests, window_seconds) in limits.items():
            r_key = self._k(f"ratelimit:{key}")
            clear_before = now - window_seconds
            pipeline.zremrangebyscore(r_key, "-inf", clear_before)
            pipeline.zcard(r_key)

        responses = pipeline.execute()
        idx = 0
        for key, (max_requests, window_seconds) in limits.items():
            # response 0: zremrangebyscore, response 1: zcard
            _ = responses[idx]
            current = int(responses[idx + 1])
            idx += 2
            result[key] = {
                "current": current,
                "limit": max_requests,
                "window_seconds": window_seconds,
                "utilization_pct": round((current / max_requests) * 100, 1) if max_requests > 0 else 0.0,
            }
        return result

    def acquire_concurrency(
        self, resource: str, limit: int, holder_id: Optional[str] = None, ttl_seconds: int = 120
    ) -> bool:
        if limit <= 0:
            return True

        h_id = holder_id or uuid.uuid4().hex
        holders_key = self._k(f"concurrency:{resource}:holders")
        lease_key = self._k(f"concurrency:{resource}:lease:{h_id}")

        if self._acquire_script is not None:
            res = self._acquire_script(
                keys=[holders_key, lease_key],
                args=[h_id, str(limit), str(ttl_seconds)],
            )
        else:
            res = self.client.eval(
                REDIS_CONCURRENCY_ACQUIRE_LUA,
                2,
                holders_key,
                lease_key,
                h_id,
                str(limit),
                str(ttl_seconds),
            )
        return bool(res == 1)

    def release_concurrency(
        self, resource: str, holder_id: Optional[str] = None
    ) -> None:
        if not holder_id:
            return

        holders_key = self._k(f"concurrency:{resource}:holders")
        lease_key = self._k(f"concurrency:{resource}:lease:{holder_id}")

        if self._release_script is not None:
            self._release_script(keys=[holders_key, lease_key], args=[holder_id])
        else:
            self.client.eval(
                REDIS_CONCURRENCY_RELEASE_LUA,
                2,
                holders_key,
                lease_key,
                holder_id,
            )

    def get_concurrency_usage(
        self, limits: Dict[str, int]
    ) -> Dict[str, Any]:
        result = {}
        for resource, limit in limits.items():
            holders_key = self._k(f"concurrency:{resource}:holders")
            members = self.client.smembers(holders_key)
            valid_count = 0
            if members:
                for m in members:
                    member_str = m.decode("utf-8") if isinstance(m, bytes) else str(m)
                    lease_key = self._k(f"concurrency:{resource}:lease:{member_str}")
                    if self.client.exists(lease_key):
                        valid_count += 1
                    else:
                        self.client.srem(holders_key, member_str)
            result[resource] = {
                "current": valid_count,
                "limit": limit,
                "utilization_pct": round((valid_count / limit) * 100, 1) if limit > 0 else 0.0,
            }
        return result

    def release_all_concurrency(self) -> None:
        try:
            pattern = self._k("concurrency:*")
            keys = self.client.keys(pattern)
            if keys:
                self.client.delete(*keys)
        except Exception as e:
            logger.warning("Failed to release all concurrency in Redis: %s", str(e))

    def record_cost_counter(self, counter_name: str, delta: int = 1) -> None:
        self.client.hincrby(self._k("cost:counters"), counter_name, delta)

    def record_external_request(self, source: str, delta: int = 1) -> None:
        self.client.hincrby(self._k("cost:sources"), source, delta)

    def get_cost_usage(self) -> Dict[str, Any]:
        raw_counters = self.client.hgetall(self._k("cost:counters")) or {}
        raw_sources = self.client.hgetall(self._k("cost:sources")) or {}

        def _decode_dict(d):
            return {
                (k.decode("utf-8") if isinstance(k, bytes) else str(k)): int(v)
                for k, v in d.items()
            }

        counters = _decode_dict(raw_counters)
        sources = _decode_dict(raw_sources)

        base_fields = [
            "embedding_calls",
            "embedding_items_total",
            "synthesis_calls",
            "estimated_input_tokens",
            "estimated_output_tokens",
            "items_processed",
            "pipeline_invocations",
        ]
        res = {f: counters.get(f, 0) for f in base_fields}
        res["external_requests"] = sources
        return res

    def reset_cost_usage(self) -> None:
        self.client.delete(self._k("cost:counters"), self._k("cost:sources"))

    def check_synthesis_rate(
        self, topic_slug: str, limit: int, window_seconds: int = 3600
    ) -> Tuple[bool, int, int]:
        key = f"synthesis:{topic_slug}"
        allowed, _, current = self.check_rate_limit(key, limit, window_seconds)
        return allowed, current, limit

    def get_synthesis_usage(
        self, limit: int, topic_slug: Optional[str] = None
    ) -> Dict[str, Any]:
        now = time.time()
        if topic_slug:
            key = self._k(f"ratelimit:synthesis:{topic_slug}")
            self.client.zremrangebyscore(key, "-inf", now - 3600)
            current = self.client.zcard(key) or 0
            return {
                "topic_slug": topic_slug,
                "current_hour": current,
                "limit": limit,
            }

        result = {}
        pattern = self._k("ratelimit:synthesis:*")
        keys = self.client.keys(pattern) or []
        for k in keys:
            k_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            slug = k_str.split("synthesis:")[-1]
            self.client.zremrangebyscore(k_str, "-inf", now - 3600)
            cnt = self.client.zcard(k_str) or 0
            if cnt > 0:
                result[slug] = {
                    "current_hour": cnt,
                    "limit": limit,
                }
        return result

    def is_healthy(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def get_backend_name(self) -> str:
        return "redis"


# ==========================================
# Governance Coordinator (Circuit-Breaker)
# ==========================================


class GovernanceCoordinator:
    """Coordinates storage backends with zero-downtime automatic fallback to memory on Redis outage."""

    def __init__(self):
        self._lock = threading.Lock()
        self.in_memory_store = InMemoryGovernanceStore()
        self.redis_store: Optional[RedisGovernanceStore] = None
        self._backend_type = os.getenv("GOVERNANCE_BACKEND", "memory").lower()
        self._fallback_allowed = os.getenv("GOVERNANCE_FALLBACK_TO_MEMORY", "true").lower() in ("true", "1", "yes")
        self._fallback_active = False
        self._last_error: Optional[str] = None
        self._last_log_time = 0.0

        if self._backend_type == "redis":
            self._init_redis()

    def _init_redis(self):
        """Attempt Redis connection initialization with strict timeouts."""
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        key_prefix = os.getenv("REDIS_KEY_PREFIX", "vantage:gov:")
        conn_timeout = float(os.getenv("REDIS_CONNECT_TIMEOUT_MS", "500")) / 1000.0
        socket_timeout = float(os.getenv("REDIS_SOCKET_TIMEOUT_MS", "500")) / 1000.0

        try:
            import redis
            client = redis.Redis.from_url(
                redis_url,
                socket_connect_timeout=conn_timeout,
                socket_timeout=socket_timeout,
                decode_responses=False,
            )
            client.ping()
            self.redis_store = RedisGovernanceStore(client, key_prefix=key_prefix)
            self._fallback_active = False
            logger.info("GovernanceCoordinator: connected to Redis shared backend (%s)", redis_url.split("@")[-1])
        except Exception as e:
            self._last_error = f"Redis connection failed: {str(e)[:100]}"
            self._fallback_active = True
            logger.warning("GovernanceCoordinator: Redis unavailable (%s). Falling back to in-memory coordination.", self._last_error)

    def _execute(self, method_name: str, *args, **kwargs) -> Any:
        """Execute method on active store with automatic fallback on failure and circuit breaker tracking."""
        from app.core.resilience import redis_governance_breaker

        # Periodic auto-recovery probe if fallback was active and breaker allows request
        if self._backend_type == "redis" and self._fallback_active:
            if redis_governance_breaker.allow_request():
                try:
                    self._init_redis()
                except Exception:
                    pass

        if self._backend_type == "redis" and self.redis_store is not None and not self._fallback_active:
            if redis_governance_breaker.allow_request():
                try:
                    method = getattr(self.redis_store, method_name)
                    res = method(*args, **kwargs)
                    redis_governance_breaker.record_success()
                    return res
                except Exception as e:
                    redis_governance_breaker.record_failure(e)
                    now = time.monotonic()
                    if now - self._last_log_time > 30.0:
                        logger.warning("Redis governance error on %s: %s. Falling back to in-process memory store.", method_name, str(e)[:100])
                        self._last_log_time = now
                    self._last_error = str(e)[:100]
                    if self._fallback_allowed:
                        self._fallback_active = True

        # In-memory execution
        method = getattr(self.in_memory_store, method_name)
        return method(*args, **kwargs)

    def get_backend_info(self) -> Dict[str, Any]:
        """Return operational metadata about active governance backend."""
        from app.core.resilience import redis_governance_breaker
        is_redis_target = self._backend_type == "redis"
        active_name = "redis" if (is_redis_target and not self._fallback_active and self.redis_store is not None) else "memory"
        is_healthy = self.in_memory_store.is_healthy() if active_name == "memory" else (self.redis_store.is_healthy() if self.redis_store else False)

        return {
            "backend": active_name,
            "configured_backend": self._backend_type,
            "is_healthy": is_healthy,
            "fallback_active": self._fallback_active,
            "fallback_allowed": self._fallback_allowed,
            "redis_configured": is_redis_target,
            "circuit_state": redis_governance_breaker.state.value,
            "last_error": self._last_error,
        }

    def reset_fallback(self) -> bool:
        """Attempt to reconnect to Redis and reset fallback state."""
        from app.core.resilience import redis_governance_breaker
        if self._backend_type == "redis":
            redis_governance_breaker.reset()
            self._init_redis()
            return not self._fallback_active
        return True


# ==========================================
# Public Facing Governors & Managers
# ==========================================


class InProcessRateLimiter:
    """Sliding-window rate limiter delegating to active coordinator store."""

    def __init__(
        self,
        limits: Optional[Dict[str, Tuple[int, int]]] = None,
        coordinator: Optional[GovernanceCoordinator] = None,
    ):
        self._coordinator = coordinator or GovernanceCoordinator()
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
        """Check if request is allowed for key. Returns (allowed, retry_after)."""
        limit_config = self._limits.get(key)
        if not limit_config:
            return True, 0.0

        max_requests, window_seconds = limit_config
        allowed, retry_after, _ = self._coordinator._execute(
            "check_rate_limit", key, max_requests, window_seconds
        )
        return allowed, retry_after

    def get_usage(self) -> Dict[str, Any]:
        """Return current rate limit utilization for all configured keys."""
        return self._coordinator._execute("get_rate_limit_usage", self._limits)

    def cleanup_expired_buckets(self, max_idle_seconds: float = 300.0) -> int:
        """Purge stale rate limit tracking buckets."""
        return self._coordinator.in_memory_store.cleanup_expired_buckets(max_idle_seconds)

    @property
    def _lock(self):
        return self._coordinator.in_memory_store._lock

    @property
    def _buckets(self):
        return self._coordinator.in_memory_store._buckets

    @property
    def _last_cleanup(self):
        return self._coordinator.in_memory_store._last_cleanup

    @_last_cleanup.setter
    def _last_cleanup(self, value):
        self._coordinator.in_memory_store._last_cleanup = value


class ResourceBudgetManager:
    """Budget limits and item clamping guards."""

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
        limit = self.budgets.get(resource)
        if limit is None:
            return True, 0, current
        return current < limit, limit, current

    def get_limit(self, resource: str) -> int:
        return self.budgets.get(resource, 0)

    def clamp(self, resource: str, value: int) -> int:
        limit = self.budgets.get(resource, value)
        if value > limit:
            logger.info("Clamping %s from %d to budget limit %d", resource, value, limit)
        return min(value, limit)

    def get_all_budgets(self) -> Dict[str, int]:
        return dict(self.budgets)


class ConcurrencyGovernor:
    """Bounded concurrency control with distributed leasing, TTL and guaranteed release."""

    def __init__(
        self,
        budget_manager: Optional[ResourceBudgetManager] = None,
        coordinator: Optional[GovernanceCoordinator] = None,
    ):
        self._coordinator = coordinator or GovernanceCoordinator()
        bm = budget_manager or ResourceBudgetManager()
        self._limits: Dict[str, int] = {
            "pipeline": bm.get_limit("max_concurrent_pipelines"),
            "source_call": bm.get_limit("max_concurrent_source_calls"),
            "backup": bm.get_limit("max_backup_operations"),
            "maintenance": bm.get_limit("max_maintenance_operations"),
        }
        self.lease_ttl_seconds = int(os.getenv("CONCURRENCY_LEASE_TTL_SECONDS", "120"))

    def acquire(self, resource: str, holder_id: Optional[str] = None) -> bool:
        limit = self._limits.get(resource, 0)
        return self._coordinator._execute(
            "acquire_concurrency", resource, limit, holder_id, self.lease_ttl_seconds
        )

    def release(self, resource: str, holder_id: Optional[str] = None):
        self._coordinator._execute("release_concurrency", resource, holder_id)

    def get_usage(self) -> Dict[str, Any]:
        return self._coordinator._execute("get_concurrency_usage", self._limits)

    @contextmanager
    def slot(self, resource: str, holder_id: Optional[str] = None):
        if not self.acquire(resource, holder_id=holder_id):
            raise RuntimeError(f"Concurrency limit reached for {resource}")
        try:
            yield
        finally:
            self.release(resource, holder_id=holder_id)

    def release_all(self):
        self._coordinator._execute("release_all_concurrency")
        logger.info("Concurrency governor: all slots released (shutdown)")


class CostTracker:
    """Operational telemetry tracking for cost-control visibility."""

    def __init__(self, coordinator: Optional[GovernanceCoordinator] = None):
        self._coordinator = coordinator or GovernanceCoordinator()

    def record_embedding_call(self, item_count: int = 0):
        self._coordinator._execute("record_cost_counter", "embedding_calls", 1)
        self._coordinator._execute("record_cost_counter", "embedding_items_total", item_count)

    def record_synthesis_call(
        self,
        estimated_input_tokens: int = 0,
        estimated_output_tokens: int = 0,
    ):
        self._coordinator._execute("record_cost_counter", "synthesis_calls", 1)
        self._coordinator._execute("record_cost_counter", "estimated_input_tokens", estimated_input_tokens)
        self._coordinator._execute("record_cost_counter", "estimated_output_tokens", estimated_output_tokens)

    def record_items_processed(self, count: int = 1):
        self._coordinator._execute("record_cost_counter", "items_processed", count)

    def record_pipeline_invocation(self):
        self._coordinator._execute("record_cost_counter", "pipeline_invocations", 1)

    def record_external_request(self, source: str):
        self._coordinator._execute("record_external_request", source, 1)

    def get_usage(self) -> Dict[str, Any]:
        return self._coordinator._execute("get_cost_usage")

    def reset(self):
        self._coordinator._execute("reset_cost_usage")


class SynthesisRateTracker:
    """Tracks synthesis calls per topic per hour to enforce budget limits."""

    def __init__(
        self,
        max_per_topic_hour: int = 5,
        coordinator: Optional[GovernanceCoordinator] = None,
    ):
        self._coordinator = coordinator or GovernanceCoordinator()
        self.max_per_topic_hour = int(
            os.getenv("BUDGET_MAX_SYNTHESIS_PER_TOPIC_HOUR", str(max_per_topic_hour))
        )

    def check_and_record(self, topic_slug: str) -> Tuple[bool, int, int]:
        return self._coordinator._execute(
            "check_synthesis_rate", topic_slug, self.max_per_topic_hour, 3600
        )

    def get_usage(self, topic_slug: Optional[str] = None) -> Dict[str, Any]:
        return self._coordinator._execute(
            "get_synthesis_usage", self.max_per_topic_hour, topic_slug
        )

    @property
    def _topic_timestamps(self):
        return self._coordinator.in_memory_store._synthesis_timestamps

    @property
    def _lock(self):
        return self._coordinator.in_memory_store._lock


@dataclass
class _ExternalSourceConfig:
    max_concurrent: int = 3
    timeout_seconds: float = 15.0
    max_retries: int = 3
    backoff_base: float = 1.0
    hourly_budget: int = 200


class ExternalRequestGovernor:
    """Governs external API requests with per-source budgets, timeouts, and retry limits."""

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
                max_retries=int(os.getenv("EXT_OPENAI_MAX_RETRIES", "3")),
                backoff_base=float(os.getenv("EXT_OPENAI_BACKOFF", "2.0")),
                hourly_budget=int(os.getenv("EXT_OPENAI_HOURLY_BUDGET", "100")),
            ),
        }
        self._active_counts: Dict[str, int] = defaultdict(int)
        self._hourly_counts: Dict[str, List[float]] = defaultdict(list)

    def check_request_allowed(self, source: str) -> Tuple[bool, str]:
        config = self._configs.get(source)
        if not config:
            return True, ""

        now = time.monotonic()
        one_hour_ago = now - 3600.0

        with self._lock:
            self._hourly_counts[source] = [
                t for t in self._hourly_counts.get(source, []) if t > one_hour_ago
            ]

            if len(self._hourly_counts[source]) >= config.hourly_budget:
                return False, f"Hourly budget exceeded ({len(self._hourly_counts[source])}/{config.hourly_budget})"

            active = self._active_counts.get(source, 0)
            if active >= config.max_concurrent:
                return False, f"Concurrent limit reached ({active}/{config.max_concurrent})"

            return True, ""

    def record_request_start(self, source: str):
        with self._lock:
            self._active_counts[source] = self._active_counts.get(source, 0) + 1
            self._hourly_counts[source].append(time.monotonic())

    def record_request_end(self, source: str):
        with self._lock:
            current = self._active_counts.get(source, 0)
            if current > 0:
                self._active_counts[source] = current - 1

    def get_config(self, source: str) -> Optional[_ExternalSourceConfig]:
        return self._configs.get(source)

    def get_usage(self) -> Dict[str, Any]:
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


class UtilizationMonitor:
    """Monitors resource utilization against configurable warning thresholds."""

    def __init__(self):
        self.warning_threshold = float(os.getenv("RESOURCE_WARNING_THRESHOLD", "0.70"))
        self.critical_threshold = float(os.getenv("RESOURCE_CRITICAL_THRESHOLD", "0.90"))

    def get_status(self, current: float, limit: float) -> str:
        if limit <= 0:
            return "normal"
        ratio = current / limit
        if ratio >= self.critical_threshold:
            return "critical"
        if ratio >= self.warning_threshold:
            return "warning"
        return "normal"

    def get_thresholds(self) -> Dict[str, float]:
        return {
            "warning_threshold": self.warning_threshold,
            "critical_threshold": self.critical_threshold,
        }


# ==========================================
# Global Singleton Instances
# ==========================================

coordinator = GovernanceCoordinator()
budget_manager = ResourceBudgetManager()
rate_limiter = InProcessRateLimiter(coordinator=coordinator)
cost_tracker = CostTracker(coordinator=coordinator)
concurrency_governor = ConcurrencyGovernor(budget_manager=budget_manager, coordinator=coordinator)
external_governor = ExternalRequestGovernor()
utilization_monitor = UtilizationMonitor()
synthesis_tracker = SynthesisRateTracker(coordinator=coordinator)
