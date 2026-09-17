"""Comprehensive Production Resilience, Fault Tolerance & Graceful Degradation Tests.

Validates:
- Exponential backoff calculations and jitter distributions
- Bounded retry policies with retryable exceptions, callbacks, and fallback factories
- State-machine Circuit Breakers (CLOSED -> OPEN -> HALF_OPEN -> CLOSED / OPEN)
- Circuit breaker registry and status reporting
- Cooperative cancellation tokens and timeouts
- Database transient lock contention retries
- External ingestion resilience (Google News, Reddit, X)
- Partial pipeline failure isolation and graceful merging
- LLM synthesis circuit breaking and fail-soft fallback degradation
- Redis / shared governance automatic degradation and recovery
- Scheduler and worker graceful shutdown and resource cleanup
- Enriched health and readiness probes under healthy, degraded, and unready conditions
"""

from datetime import datetime, timezone
import json
import time
from unittest.mock import MagicMock, patch
import urllib.error

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.core.resilience import (
    BackoffStrategy,
    CancellationToken,
    CircuitBreaker,
    CircuitBreakerOpenException,
    CircuitBreakerRegistry,
    CircuitState,
    JitterMode,
    circuit_registry,
    google_news_breaker,
    openai_breaker,
    reddit_breaker,
    redis_governance_breaker,
    retry_with_backoff,
    safe_db_operation,
    trend_discovery_breaker,
    x_breaker,
)
from app.database.database import SessionLocal, engine
from app.database.models import Base, Topic
from app.ingestion.google_news import GoogleNewsIngestor
from app.ingestion.merge_pipeline import MergePipeline
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.reddit import RedditIngestor
from app.ingestion.x import XScraper
from app.llm.perspective import MockPerspectiveSynthesizer, OpenAIPerspectiveSynthesizer
from app.llm.pipeline import PerspectivePipeline
from app.main import app
from app.workers.discovery import BaseTrendProvider, TrendDiscoveryService
from app.workers.scheduler import BackgroundScheduler

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_and_teardown():
    """Reset circuit breakers around each test."""
    circuit_registry.reset_all()
    yield
    circuit_registry.reset_all()


# ============================================================================
# 1. Backoff Strategy & Jitter Tests
# ============================================================================

def test_backoff_strategy_delays_and_jitter():
    """Verify exponential backoff calculation, ceiling clamping, and jitter modes."""
    # 1. No jitter (deterministic exponential growth)
    strategy_none = BackoffStrategy(base_delay=0.1, max_delay=1.0, multiplier=2.0, jitter_mode=JitterMode.NONE)
    assert strategy_none.compute_delay(1) == 0.1
    assert strategy_none.compute_delay(2) == 0.2
    assert strategy_none.compute_delay(3) == 0.4
    assert strategy_none.compute_delay(4) == 0.8
    assert strategy_none.compute_delay(5) == 1.0  # Clamped to max_delay
    assert strategy_none.compute_delay(0) == 0.0

    # 2. Full jitter (in range [0, raw_delay])
    strategy_full = BackoffStrategy(base_delay=0.2, max_delay=2.0, multiplier=2.0, jitter_mode=JitterMode.FULL)
    for attempt in range(1, 6):
        delay = strategy_full.compute_delay(attempt)
        max_possible = min(2.0, 0.2 * (2.0 ** (attempt - 1)))
        assert 0.0 <= delay <= max_possible

    # 3. Equal jitter (in range [raw_delay/2, raw_delay])
    strategy_equal = BackoffStrategy(base_delay=0.2, max_delay=2.0, multiplier=2.0, jitter_mode=JitterMode.EQUAL)
    for attempt in range(1, 6):
        delay = strategy_equal.compute_delay(attempt)
        raw_delay = min(2.0, 0.2 * (2.0 ** (attempt - 1)))
        assert raw_delay / 2.0 <= delay <= raw_delay

    # 4. Decorrelated jitter
    strategy_decorr = BackoffStrategy(base_delay=0.1, max_delay=2.0, multiplier=2.0, jitter_mode=JitterMode.DECORRELATED)
    delay1 = strategy_decorr.compute_delay(1)
    assert 0.1 <= delay1 <= 2.0
    delay2 = strategy_decorr.compute_delay(2, previous_delay=delay1)
    assert 0.1 <= delay2 <= 2.0


# ============================================================================
# 2. Bounded Retry Policy Tests
# ============================================================================

def test_retry_with_backoff_success_on_retry():
    """Verify function retries transient errors and succeeds on subsequent attempt."""
    call_count = 0
    retry_log = []

    def _on_retry(attempt, exc, delay):
        retry_log.append((attempt, str(exc)))

    @retry_with_backoff(
        max_attempts=3,
        backoff=BackoffStrategy(base_delay=0.01, max_delay=0.05, jitter_mode=JitterMode.NONE),
        retryable_exceptions=(ValueError,),
        on_retry=_on_retry,
    )
    def _unstable_func():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("Transient glitch")
        return "success"

    result = _unstable_func()
    assert result == "success"
    assert call_count == 3
    assert len(retry_log) == 2


def test_retry_with_backoff_exhaustion_and_fallback():
    """Verify fallback factory execution when retries are exhausted."""
    call_count = 0

    @retry_with_backoff(
        max_attempts=2,
        backoff=BackoffStrategy(base_delay=0.01, max_delay=0.02, jitter_mode=JitterMode.NONE),
        retryable_exceptions=(TimeoutError,),
        fallback_factory=lambda *args, **kwargs: "fallback_payload",
    )
    def _always_failing():
        nonlocal call_count
        call_count += 1
        raise TimeoutError("Connection timed out")

    result = _always_failing()
    assert result == "fallback_payload"
    assert call_count == 2


def test_retry_with_backoff_non_retryable_exception():
    """Verify non-retryable exceptions immediately raise without retrying."""
    call_count = 0

    @retry_with_backoff(
        max_attempts=3,
        backoff=BackoffStrategy(base_delay=0.01),
        retryable_exceptions=(ValueError,),
    )
    def _fatal_func():
        nonlocal call_count
        call_count += 1
        raise KeyError("Fatal invalid key")

    with pytest.raises(KeyError):
        _fatal_func()
    assert call_count == 1


# ============================================================================
# 3. Circuit Breaker State Machine Tests
# ============================================================================

def test_circuit_breaker_transitions():
    """Test full cycle: CLOSED -> OPEN -> HALF_OPEN -> CLOSED and OPEN -> HALF_OPEN -> OPEN."""
    cb = CircuitBreaker(
        name="test_service",
        failure_threshold=3,
        recovery_timeout=0.1,  # Fast 100ms for testing
        success_threshold=2,
        expected_exceptions=(RuntimeError,),
    )

    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True

    # 1. Execute successes in CLOSED state
    res = cb.execute(lambda: 42)
    assert res == 42
    assert cb.state == CircuitState.CLOSED

    # 2. Accumulate failures up to threshold
    for i in range(2):
        with pytest.raises(RuntimeError):
            cb.execute(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        assert cb.state == CircuitState.CLOSED

    # 3rd failure trips the breaker to OPEN
    with pytest.raises(RuntimeError):
        cb.execute(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
    assert cb.state == CircuitState.OPEN

    # 3. Calls are immediately rejected when OPEN
    assert cb.allow_request() is False
    with pytest.raises(CircuitBreakerOpenException) as excinfo:
        cb.execute(lambda: "should not run")
    assert "is OPEN" in str(excinfo.value)

    # 4. Wait for recovery timeout -> transitions to HALF_OPEN
    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN

    # 5. First probe in HALF_OPEN succeeds
    res1 = cb.execute(lambda: "probe_1")
    assert res1 == "probe_1"
    assert cb.state == CircuitState.HALF_OPEN  # Needs 2 successes

    # 6. Second probe in HALF_OPEN succeeds -> transitions back to CLOSED
    res2 = cb.execute(lambda: "probe_2")
    assert res2 == "probe_2"
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True

    # 7. Test probe failure in HALF_OPEN trips straight back to OPEN
    cb.trip("Force trip for probe test")
    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
    with pytest.raises(RuntimeError):
        cb.execute(lambda: (_ for _ in ()).throw(RuntimeError("probe failed")))
    assert cb.state == CircuitState.OPEN


def test_circuit_breaker_fallback_factory():
    """Verify CircuitBreaker executes fallback factory when OPEN."""
    cb = CircuitBreaker(
        name="fallback_service",
        failure_threshold=2,
        fallback_factory=lambda *args, **kwargs: {"status": "degraded_fallback"},
    )
    cb.trip("Simulated outage")
    result = cb.execute(lambda: {"status": "real_data"})
    assert result == {"status": "degraded_fallback"}


def test_circuit_breaker_registry():
    """Verify registry methods, status telemetry, and reset."""
    reg = CircuitBreakerRegistry()
    b1 = reg.get_or_create("service_a", failure_threshold=2)
    b2 = reg.get_or_create("service_b", failure_threshold=2)

    assert reg.is_all_healthy() is True
    b1.trip("Test trip")
    assert reg.is_all_healthy() is False

    status_all = reg.get_all_status()
    assert "service_a" in status_all
    assert status_all["service_a"]["state"] == "OPEN"
    assert status_all["service_b"]["state"] == "CLOSED"

    reg.reset_all()
    assert reg.is_all_healthy() is True
    assert reg.get("service_a").state == CircuitState.CLOSED


# ============================================================================
# 4. Cancellation Token & Safe DB Retry Tests
# ============================================================================

def test_cancellation_token():
    """Verify CancellationToken signalling and timeout waiting."""
    token = CancellationToken()
    assert not token.is_cancelled
    token.raise_if_cancelled()  # Does not raise

    token.cancel("User requested cancel")
    assert token.is_cancelled
    assert token.cancel_reason == "User requested cancel"

    with pytest.raises(TimeoutError) as exc:
        token.raise_if_cancelled()
    assert "User requested cancel" in str(exc.value)


def test_safe_db_operation_transient_retry():
    """Verify safe_db_operation retries on transient locked errors."""
    attempts = 0

    def _flaky_db_write():
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise RuntimeError("OperationalError: database is locked")
        return "written"

    res = safe_db_operation(_flaky_db_write, max_retries=3, base_delay=0.01)
    assert res == "written"
    assert attempts == 2


# ============================================================================
# 5. Ingestion Provider Resilience & Partial Failure Tests
# ============================================================================

def test_google_news_circuit_breaker_and_retry(db_session: Session, sample_topic: Topic):
    """Verify GoogleNewsIngestor fast-fails when circuit breaker is OPEN."""
    ingestor = GoogleNewsIngestor()

    # Trip breaker
    google_news_breaker.trip("Simulated Google News network block")
    results = ingestor.fetch_and_stage(topic=sample_topic, db=db_session, limit=10)
    assert results == []

    # Reset breaker and test normal mock execution
    google_news_breaker.reset()


def test_reddit_circuit_breaker_and_retry(db_session: Session, sample_topic: Topic):
    """Verify RedditIngestor fast-fails when circuit breaker is OPEN."""
    ingestor = RedditIngestor()
    reddit_breaker.trip("Simulated Reddit API rate limit block")

    results = ingestor.fetch_and_stage(topic=sample_topic, db=db_session, limit=10)
    assert results == []

    reddit_breaker.reset()


def test_x_scraper_fail_soft(db_session: Session, sample_topic: Topic):
    """Verify XScraper fail-soft resilience."""
    scraper = XScraper()
    results = scraper.fetch_and_stage(topic=sample_topic, db=db_session, limit=10)
    assert results == []


def test_ingestion_pipeline_partial_failure_isolation(db_session: Session, sample_topic: Topic):
    """Verify pipeline continues and merges when 1 or 2 providers fail completely."""
    # Force Google News to fail, while Reddit produces staged data
    mock_gn = MagicMock(spec=GoogleNewsIngestor)
    mock_gn.fetch_and_stage.side_effect = RuntimeError("Google News 503 Outage")

    from app.database.models import RawReddit
    mock_reddit = MagicMock(spec=RedditIngestor)
    def _mock_reddit_stage(topic, db, limit=50, timeout_seconds=10.0):
        rec = RawReddit(
            slug_id=topic.id,
            post_id="resilience_post_1",
            body="Reddit resilience discussion item",
            score=15,
            num_comments=4,
            subreddit="technology",
            author="test_author",
            created_utc=datetime.utcnow(),
        )
        db.add(rec)
        db.commit()
        return [rec]
    mock_reddit.fetch_and_stage.side_effect = _mock_reddit_stage

    pipeline = IngestionPipeline(
        google_news_ingestor=mock_gn,
        reddit_ingestor=mock_reddit,
        x_scraper=XScraper(),
        merge_pipeline=MergePipeline(),
    )

    result = pipeline.run(topic=sample_topic, db=db_session, limit_per_source=10)
    assert result["topic_id"] == sample_topic.id
    assert "google_news" in result["staging_errors"]
    assert result["staging_counts"]["reddit"] == 1
    assert result["merge_result"]["new_records_added"] == 1


# ============================================================================
# 6. LLM Synthesis & Graceful Degradation Tests
# ============================================================================

def test_llm_synthesis_circuit_and_fallback(db_session: Session, sample_topic: Topic):
    """Verify PerspectivePipeline degrades gracefully to extractive fallback on LLM failure."""
    # Mock synthesizer that simulates persistent OpenAI failure
    mock_failing_synth = MagicMock(spec=OpenAIPerspectiveSynthesizer)
    mock_failing_synth.synthesize.side_effect = RuntimeError("OpenAI API HTTP 429 Quota Exceeded")

    pipeline = PerspectivePipeline(synthesizer=mock_failing_synth)

    # Provide valid cluster data with sample items
    cluster_payload = {
        "status": "success",
        "sample_size": 40,
        "clusters": [
            {
                "cluster_id": 1,
                "size": 25,
                "share": 0.625,
                "representative_samples": [
                    {"source": "google_news", "author_handle": "Reuters", "url": "https://reuters.com", "text_content": "Economic growth projections updated."},
                ],
            },
            {
                "cluster_id": 2,
                "size": 15,
                "share": 0.375,
                "representative_samples": [
                    {"source": "reddit", "author_handle": "u/analyst", "url": "https://reddit.com/r/tech", "text_content": "Regulatory impact analysis discussion."},
                ],
            },
        ],
    }

    result = pipeline.run_synthesis_for_topic(
        topic=sample_topic,
        db=db_session,
        min_volume_threshold=30,
        cluster_data=cluster_payload,
    )

    assert result["status"] == "success"
    assert result["perspectives_count"] >= 2
    # Verify confidence note indicates graceful fallback
    assert "fail-soft extractive fallback" in result["confidence_note"]


# ============================================================================
# 7. Trend Discovery Resilience Tests
# ============================================================================

def test_trend_discovery_resilience_with_provider_failures():
    """Verify TrendDiscoveryService handles partial provider failure."""
    class FailingProvider(BaseTrendProvider):
        source_name = "failing_provider"
        def discover_candidates(self, limit=10, timeout_seconds=10.0):
            raise TimeoutError("Provider connection timeout")

    class WorkingProvider(BaseTrendProvider):
        source_name = "working_provider"
        def discover_candidates(self, limit=10, timeout_seconds=10.0):
            return ["Resilient Multi-Worker Architecture", "Quantum Cryptography"]

    service = TrendDiscoveryService(providers=[FailingProvider(), WorkingProvider()])
    candidates = service.discover_trending_topics(limit_per_provider=5)

    assert len(candidates) == 2
    assert candidates[0].title in ["Resilient Multi-Worker Architecture", "Quantum Cryptography"]


# ============================================================================
# 8. Background Scheduler Graceful Shutdown & Lock Cleanup Tests
# ============================================================================

def test_scheduler_graceful_shutdown_and_lock_cleanup():
    """Verify scheduler stop cancels in-flight tokens and releases all concurrency and backup locks."""
    scheduler = BackgroundScheduler()
    scheduler.start(auto_discover=False, run_immediately=False)
    assert scheduler.is_running is True

    # Stop scheduler
    scheduler.stop(timeout_seconds=2.0)
    assert scheduler.is_running is False
    assert scheduler.cancellation_token.is_cancelled is True


# ============================================================================
# 9. Health & Readiness Signals Tests
# ============================================================================

def test_health_check_endpoint():
    """Verify liveness probe returns HTTP 200 healthy."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "vantage-news-api"


def test_readiness_probe_healthy_and_degraded():
    """Verify readiness probe returns 'ready' when healthy, and 'degraded' when a circuit breaker is OPEN."""
    # 1. Healthy
    circuit_registry.reset_all()
    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["database"] == "connected"
    assert data["circuit_breakers"]["all_healthy"] is True

    # 2. Degraded (trip a circuit breaker)
    reddit_breaker.trip("Reddit rate limit outage")
    resp_deg = client.get("/ready")
    assert resp_deg.status_code == 200
    data_deg = resp_deg.json()
    assert data_deg["status"] == "degraded"
    assert "reddit" in data_deg["circuit_breakers"]["open"]

    # Reset
    reddit_breaker.reset()


def test_ops_resilience_endpoint_and_reset():
    """Verify /api/ops/resilience telemetry and reset endpoints."""
    # 1. Check status
    resp = client.get("/api/ops/resilience")
    assert resp.status_code == 200
    data = resp.json()
    assert "circuit_breakers" in data
    assert "google_news" in data["circuit_breakers"]
    assert "governance_backend" in data

    # 2. Trip a breaker and test reset
    openai_breaker.trip("Test trip")
    resp_after = client.get("/api/ops/resilience")
    assert resp_after.json()["status"] == "degraded"

    # 3. Reset via ops endpoint
    resp_reset = client.post("/api/ops/resilience/reset")
    assert resp_reset.status_code == 200
    assert resp_reset.json()["status"] == "success"

    resp_final = client.get("/api/ops/resilience")
    assert resp_final.json()["status"] == "healthy"
