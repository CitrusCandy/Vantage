"""Production Resilience, Fault Tolerance & Graceful Degradation Framework for Vantage News.

Provides robust, thread-safe primitives for:
- Bounded retries with exponential backoff and full/decorrelated jitter
- State-machine Circuit Breakers (CLOSED, OPEN, HALF_OPEN) with automatic probe recovery
- Centralized CircuitBreakerRegistry for system-wide observability
- Cooperative cancellation tokens and bounded execution timeouts
- Database transient lock retry policies
- Safe degradation fallbacks for external dependencies and shared infrastructure
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import functools
import logging
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type, Union

logger = logging.getLogger("app.core.resilience")


# ============================================================================
# 1. Backoff Strategies & Jitter
# ============================================================================

class JitterMode(str, Enum):
    FULL = "full"
    EQUAL = "equal"
    DECORRELATED = "decorrelated"
    NONE = "none"


@dataclass
class BackoffStrategy:
    """Configurable exponential backoff calculator with jitter."""

    base_delay: float = 0.5
    max_delay: float = 10.0
    multiplier: float = 2.0
    jitter_mode: JitterMode = JitterMode.FULL

    def compute_delay(self, attempt: int, previous_delay: Optional[float] = None) -> float:
        """Compute sleep duration in seconds for a given attempt (1-indexed)."""
        if attempt <= 0:
            return 0.0

        raw_delay = min(self.max_delay, self.base_delay * (self.multiplier ** (attempt - 1)))

        if self.jitter_mode == JitterMode.FULL:
            # Full jitter: Uniform(0, raw_delay)
            return random.uniform(0.0, raw_delay)
        elif self.jitter_mode == JitterMode.EQUAL:
            # Equal jitter: raw_delay/2 + Uniform(0, raw_delay/2)
            half = raw_delay / 2.0
            return half + random.uniform(0.0, half)
        elif self.jitter_mode == JitterMode.DECORRELATED:
            # Decorrelated jitter: min(max_delay, Uniform(base_delay, prev_delay * 3))
            prev = previous_delay if previous_delay is not None else self.base_delay
            return min(self.max_delay, random.uniform(self.base_delay, prev * 3.0))
        else:
            # No jitter
            return raw_delay


# ============================================================================
# 2. Bounded Retry Policy & Decorator
# ============================================================================

def retry_with_backoff(
    max_attempts: int = 3,
    backoff: Optional[BackoffStrategy] = None,
    retryable_exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
    reraise_last: bool = True,
    fallback_factory: Optional[Callable[..., Any]] = None,
):
    """Decorator and wrapper for executing functions with bounded exponential backoff retries."""
    strategy = backoff or BackoffStrategy()

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            prev_delay: Optional[float] = None
            last_err: Optional[BaseException] = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_err = exc
                    if attempt >= max_attempts:
                        logger.warning(
                            "Retry exhausted (%d/%d) for %s: %s",
                            attempt,
                            max_attempts,
                            func.__name__,
                            str(exc),
                        )
                        break

                    delay = strategy.compute_delay(attempt, prev_delay)
                    prev_delay = delay

                    if on_retry:
                        try:
                            on_retry(attempt, exc, delay)
                        except Exception as cb_err:
                            logger.error("on_retry callback error: %s", cb_err)

                    logger.info(
                        "Attempt %d/%d failed for %s (%s). Retrying in %.3fs...",
                        attempt,
                        max_attempts,
                        func.__name__,
                        str(exc),
                        delay,
                    )
                    time.sleep(delay)

            if fallback_factory is not None:
                return fallback_factory(*args, **kwargs)

            if reraise_last and last_err:
                raise last_err
            return None

        return wrapper

    return decorator


# ============================================================================
# 3. Circuit Breaker
# ============================================================================

class CircuitState(str, Enum):
    CLOSED = "CLOSED"        # Normal operations: calls proceed directly
    OPEN = "OPEN"            # Tripped: fail fast without executing call
    HALF_OPEN = "HALF_OPEN"  # Testing: limited probe requests allowed to test recovery


class CircuitBreakerOpenException(Exception):
    """Raised when a call is rejected because the circuit breaker is in OPEN state."""

    def __init__(self, name: str, retry_after: float):
        self.name = name
        self.retry_after = retry_after
        super().__init__(f"Circuit breaker '{name}' is OPEN. Retry probe after {retry_after:.1f}s.")


@dataclass
class CircuitBreakerMetrics:
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    short_circuited_calls: int = 0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_state_change: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_failure_time: Optional[datetime] = None
    last_error: Optional[str] = None


class CircuitBreaker:
    """Thread-safe circuit breaker protecting against cascading third-party failures."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 4,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
        expected_exceptions: Tuple[Type[BaseException], ...] = (Exception,),
        fallback_factory: Optional[Callable[..., Any]] = None,
    ):
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout = max(0.01, recovery_timeout)
        self.success_threshold = max(1, success_threshold)
        self.expected_exceptions = expected_exceptions
        self.fallback_factory = fallback_factory

        self._state = CircuitState.CLOSED
        self._lock = threading.Lock()
        self._metrics = CircuitBreakerMetrics()
        self._last_state_change_ts = time.time()
        self._half_open_probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._evaluate_state_transition()
            return self._state

    def _evaluate_state_transition(self) -> None:
        """Internal helper to transition from OPEN to HALF_OPEN when recovery timeout passes."""
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._last_state_change_ts
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._last_state_change_ts = time.time()
                self._metrics.last_state_change = datetime.now(timezone.utc)
                self._metrics.consecutive_successes = 0
                self._half_open_probe_in_flight = False
                logger.info("Circuit breaker '%s' transitioned to HALF_OPEN (recovery probe)", self.name)

    def allow_request(self) -> bool:
        """Check if request is permitted without throwing an exception."""
        with self._lock:
            self._evaluate_state_transition()
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.HALF_OPEN:
                if not self._half_open_probe_in_flight:
                    self._half_open_probe_in_flight = True
                    return True
                return False
            # OPEN
            return False

    def record_success(self) -> None:
        """Record a successful execution, updating metrics and closing circuit if in HALF_OPEN."""
        with self._lock:
            self._metrics.total_calls += 1
            self._metrics.successful_calls += 1
            self._metrics.consecutive_failures = 0

            if self._state == CircuitState.HALF_OPEN:
                self._metrics.consecutive_successes += 1
                self._half_open_probe_in_flight = False
                if self._metrics.consecutive_successes >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._last_state_change_ts = time.time()
                    self._metrics.last_state_change = datetime.now(timezone.utc)
                    self._metrics.last_error = None
                    logger.info("Circuit breaker '%s' recovered and transitioned to CLOSED", self.name)

    def record_failure(self, error: BaseException) -> None:
        """Record a failure, incrementing failure counters and opening circuit if threshold reached."""
        with self._lock:
            self._metrics.total_calls += 1
            self._metrics.failed_calls += 1
            self._metrics.consecutive_failures += 1
            self._metrics.last_failure_time = datetime.now(timezone.utc)
            self._metrics.last_error = str(error)[:200]
            self._half_open_probe_in_flight = False

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed, trip straight back to OPEN
                self._state = CircuitState.OPEN
                self._last_state_change_ts = time.time()
                self._metrics.last_state_change = datetime.now(timezone.utc)
                logger.warning("Circuit breaker '%s' probe failed -> transitioned to OPEN: %s", self.name, error)
            elif self._state == CircuitState.CLOSED:
                if self._metrics.consecutive_failures >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._last_state_change_ts = time.time()
                    self._metrics.last_state_change = datetime.now(timezone.utc)
                    logger.error(
                        "Circuit breaker '%s' tripped OPEN (%d consecutive failures): %s",
                        self.name,
                        self._metrics.consecutive_failures,
                        error,
                    )

    def get_retry_after(self) -> float:
        """Remaining seconds before the circuit breaker allows a recovery probe."""
        with self._lock:
            if self._state != CircuitState.OPEN:
                return 0.0
            elapsed = time.time() - self._last_state_change_ts
            return max(0.0, self.recovery_timeout - elapsed)

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute callable wrapped with circuit breaker protection."""
        if not self.allow_request():
            with self._lock:
                self._metrics.short_circuited_calls += 1
            retry_after = self.get_retry_after()
            if self.fallback_factory is not None:
                logger.debug(
                    "Circuit breaker '%s' is OPEN. Executing configured fallback.",
                    self.name,
                )
                return self.fallback_factory(*args, **kwargs)
            raise CircuitBreakerOpenException(self.name, retry_after)

        try:
            res = func(*args, **kwargs)
            self.record_success()
            return res
        except self.expected_exceptions as exc:
            self.record_failure(exc)
            if self.fallback_factory is not None:
                logger.warning(
                    "Call failed for '%s' (%s). Executing fallback.",
                    self.name,
                    str(exc),
                )
                return self.fallback_factory(*args, **kwargs)
            raise

    def __call__(self, func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return self.execute(func, *args, **kwargs)
        return wrapper

    def reset(self) -> None:
        """Manually reset circuit breaker to healthy CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._metrics = CircuitBreakerMetrics()
            self._last_state_change_ts = time.time()
            self._half_open_probe_in_flight = False

    def trip(self, reason: str = "manually tripped") -> None:
        """Manually force circuit breaker into OPEN state."""
        with self._lock:
            self._state = CircuitState.OPEN
            self._last_state_change_ts = time.time()
            self._metrics.last_state_change = datetime.now(timezone.utc)
            self._metrics.last_error = reason

    def get_status(self) -> Dict[str, Any]:
        """Return comprehensive telemetry payload for observability."""
        with self._lock:
            self._evaluate_state_transition()
            retry_after = max(0.0, self.recovery_timeout - (time.time() - self._last_state_change_ts)) if self._state == CircuitState.OPEN else 0.0
            return {
                "name": self.name,
                "state": self._state.value,
                "is_healthy": self._state == CircuitState.CLOSED,
                "is_degraded": self._state in (CircuitState.OPEN, CircuitState.HALF_OPEN),
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_seconds": self.recovery_timeout,
                "retry_after_seconds": round(retry_after, 2),
                "total_calls": self._metrics.total_calls,
                "successful_calls": self._metrics.successful_calls,
                "failed_calls": self._metrics.failed_calls,
                "short_circuited_calls": self._metrics.short_circuited_calls,
                "consecutive_failures": self._metrics.consecutive_failures,
                "last_state_change": self._metrics.last_state_change.isoformat(),
                "last_failure_time": self._metrics.last_failure_time.isoformat() if self._metrics.last_failure_time else None,
                "last_error": self._metrics.last_error,
            }


# ============================================================================
# 4. Centralized Circuit Breaker Registry
# ============================================================================

class CircuitBreakerRegistry:
    """Registry maintaining all circuit breakers across the platform."""

    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get_or_create(
        self,
        name: str,
        failure_threshold: int = 4,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
        expected_exceptions: Tuple[Type[BaseException], ...] = (Exception,),
        fallback_factory: Optional[Callable[..., Any]] = None,
    ) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    recovery_timeout=recovery_timeout,
                    success_threshold=success_threshold,
                    expected_exceptions=expected_exceptions,
                    fallback_factory=fallback_factory,
                )
            return self._breakers[name]

    def get(self, name: str) -> Optional[CircuitBreaker]:
        with self._lock:
            return self._breakers.get(name)

    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Return status snapshot of all registered breakers."""
        with self._lock:
            return {name: cb.get_status() for name, cb in self._breakers.items()}

    def is_all_healthy(self) -> bool:
        """Returns True if every registered breaker is currently CLOSED."""
        with self._lock:
            return all(cb.state == CircuitState.CLOSED for cb in self._breakers.values())

    def reset_all(self) -> None:
        """Reset all circuit breakers (for testing/recovery)."""
        with self._lock:
            for cb in self._breakers.values():
                cb.reset()


# Global Registry Instance
circuit_registry = CircuitBreakerRegistry()

# Initialize standard provider breakers
google_news_breaker = circuit_registry.get_or_create("google_news", failure_threshold=4, recovery_timeout=30.0)
reddit_breaker = circuit_registry.get_or_create("reddit", failure_threshold=4, recovery_timeout=30.0)
x_breaker = circuit_registry.get_or_create("x", failure_threshold=4, recovery_timeout=30.0)
openai_breaker = circuit_registry.get_or_create("openai_synthesis", failure_threshold=3, recovery_timeout=45.0)
embeddings_breaker = circuit_registry.get_or_create("embeddings", failure_threshold=4, recovery_timeout=30.0)
redis_governance_breaker = circuit_registry.get_or_create("redis_governance", failure_threshold=3, recovery_timeout=20.0)
trend_discovery_breaker = circuit_registry.get_or_create("trend_discovery", failure_threshold=4, recovery_timeout=30.0)


# ============================================================================
# 5. Cancellation Token & Timeout Scope
# ============================================================================

class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self):
        self._event = threading.Event()
        self._reason: Optional[str] = None

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def cancel_reason(self) -> Optional[str]:
        return self._reason

    def cancel(self, reason: str = "operation cancelled") -> None:
        self._reason = reason
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise TimeoutError(f"Operation cancelled: {self._reason or 'timeout/shutdown'}")

    def wait(self, timeout_seconds: float) -> bool:
        """Wait for cancellation event or timeout. Returns True if cancelled before timeout."""
        return self._event.wait(timeout=timeout_seconds)


# ============================================================================
# 6. Database Transient Retry Helper
# ============================================================================

def safe_db_operation(
    db_func: Callable,
    max_retries: int = 3,
    base_delay: float = 0.05,
    max_delay: float = 0.5,
) -> Any:
    """Execute a database operation with retries for transient locks / busy SQLite connections."""
    backoff = BackoffStrategy(
        base_delay=base_delay,
        max_delay=max_delay,
        multiplier=2.0,
        jitter_mode=JitterMode.FULL,
    )

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            return db_func()
        except Exception as exc:
            last_exc = exc
            err_str = str(exc).lower()
            is_transient = any(
                term in err_str for term in ("locked", "busy", "timeout", "deadlock", "concurrent")
            )
            if not is_transient or attempt >= max_retries:
                raise
            delay = backoff.compute_delay(attempt)
            logger.debug("Database operation encountered transient lock (%s). Retrying in %.3fs...", exc, delay)
            time.sleep(delay)

    if last_exc:
        raise last_exc
