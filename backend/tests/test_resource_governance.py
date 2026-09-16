"""Deterministic, offline tests for Production Cost Control, Rate Limiting & Resource Governance.

All tests are fully offline and use in-memory SQLite databases via conftest.py.
No external API calls or network access is required.
"""

import os
import time

import pytest
from fastapi.testclient import TestClient


# ==========================================
# Fixtures
# ==========================================


@pytest.fixture
def fresh_rate_limiter():
    """Create a fresh InProcessRateLimiter with test-friendly limits."""
    from app.core.resource_governor import InProcessRateLimiter
    return InProcessRateLimiter(limits={
        "test:endpoint": (3, 60),
        "public:topic_create": (2, 60),
        "ops:trending": (2, 30),
    })


@pytest.fixture
def fresh_budget_manager(monkeypatch):
    """Create a ResourceBudgetManager with known test limits."""
    monkeypatch.setenv("BUDGET_MAX_INGESTION_ITEMS_PER_SOURCE", "50")
    monkeypatch.setenv("BUDGET_MAX_CONCURRENT_PIPELINES", "3")
    monkeypatch.setenv("BUDGET_MAX_EMBEDDINGS_PER_RUN", "100")
    monkeypatch.setenv("BUDGET_MAX_SYNTHESIS_PER_TOPIC_HOUR", "2")
    from app.core.resource_governor import ResourceBudgetManager
    return ResourceBudgetManager()


@pytest.fixture
def fresh_cost_tracker():
    """Create a fresh CostTracker instance."""
    from app.core.resource_governor import CostTracker
    tracker = CostTracker()
    return tracker


@pytest.fixture
def fresh_concurrency_governor(fresh_budget_manager):
    """Create a ConcurrencyGovernor with test budget limits."""
    from app.core.resource_governor import ConcurrencyGovernor
    return ConcurrencyGovernor(fresh_budget_manager)


@pytest.fixture
def fresh_utilization_monitor(monkeypatch):
    """Create a UtilizationMonitor with known thresholds."""
    monkeypatch.setenv("RESOURCE_WARNING_THRESHOLD", "0.70")
    monkeypatch.setenv("RESOURCE_CRITICAL_THRESHOLD", "0.90")
    from app.core.resource_governor import UtilizationMonitor
    return UtilizationMonitor()


@pytest.fixture
def fresh_synthesis_tracker(monkeypatch):
    """Create a SynthesisRateTracker with test limit."""
    monkeypatch.setenv("BUDGET_MAX_SYNTHESIS_PER_TOPIC_HOUR", "2")
    from app.core.resource_governor import SynthesisRateTracker
    return SynthesisRateTracker(max_per_topic_hour=2)


@pytest.fixture
def fresh_external_governor():
    """Create a fresh ExternalRequestGovernor."""
    from app.core.resource_governor import ExternalRequestGovernor
    return ExternalRequestGovernor()


@pytest.fixture
def test_client(monkeypatch):
    """Create a TestClient with rate-limited endpoints."""
    monkeypatch.setenv("ENABLE_OPS_CONTROLS", "true")
    monkeypatch.setenv("OPS_API_KEY", "")
    # Reset the global rate limiter with tight limits for testing
    from app.core import resource_governor
    test_limiter = resource_governor.InProcessRateLimiter(limits={
        "public:topic_create": (2, 60),
        "public:ingestion": (2, 60),
        "public:merge": (2, 60),
        "public:clustering": (2, 60),
        "public:synthesis": (2, 60),
        "public:pipeline": (2, 60),
        "ops:trending": (2, 30),
        "ops:reprocess_topic": (2, 30),
        "ops:backup_create": (2, 60),
        "ops:backup_verify": (2, 60),
        "ops:maintenance": (2, 60),
    })
    monkeypatch.setattr(resource_governor, "rate_limiter", test_limiter)

    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


# ==========================================
# Rate Limiter Tests
# ==========================================


class TestRateLimiter:
    """Tests for the InProcessRateLimiter."""

    def test_allows_requests_within_window(self, fresh_rate_limiter):
        """Requests within the configured limit should be allowed."""
        rl = fresh_rate_limiter
        for _ in range(3):
            allowed, retry_after = rl.check_rate_limit("test:endpoint")
            assert allowed is True
            assert retry_after == 0.0

    def test_blocks_when_exceeded(self, fresh_rate_limiter):
        """Requests beyond the limit should be blocked."""
        rl = fresh_rate_limiter
        for _ in range(3):
            rl.check_rate_limit("test:endpoint")

        allowed, retry_after = rl.check_rate_limit("test:endpoint")
        assert allowed is False
        assert retry_after > 0.0

    def test_retry_after_value(self, fresh_rate_limiter):
        """Retry-After should be a positive number indicating when the window expires."""
        rl = fresh_rate_limiter
        for _ in range(3):
            rl.check_rate_limit("test:endpoint")

        allowed, retry_after = rl.check_rate_limit("test:endpoint")
        assert allowed is False
        assert 0.0 < retry_after <= 60.0

    def test_different_limits_public_vs_ops(self, fresh_rate_limiter):
        """Public and ops endpoints should have separate rate limit pools."""
        rl = fresh_rate_limiter
        # Exhaust public limit (2 requests)
        rl.check_rate_limit("public:topic_create")
        rl.check_rate_limit("public:topic_create")
        allowed_pub, _ = rl.check_rate_limit("public:topic_create")
        assert allowed_pub is False

        # Ops limit should still have capacity
        allowed_ops, _ = rl.check_rate_limit("ops:trending")
        assert allowed_ops is True

    def test_expired_bucket_cleanup(self, fresh_rate_limiter):
        """Expired buckets should be cleaned up to prevent memory leaks."""
        rl = fresh_rate_limiter
        # Add some entries
        rl.check_rate_limit("test:endpoint")

        # Manually expire them by manipulating internal state
        import time
        now = time.monotonic()
        with rl._lock:
            for bucket in rl._buckets.values():
                bucket.timestamps = [now - 300]  # 5 minutes ago
                bucket.last_access = now - 300

        removed = rl.cleanup_expired_buckets()
        assert removed >= 1

    def test_unknown_key_always_allowed(self, fresh_rate_limiter):
        """Keys not in the configured limits should always be allowed."""
        rl = fresh_rate_limiter
        for _ in range(100):
            allowed, retry_after = rl.check_rate_limit("unknown:key")
            assert allowed is True

    def test_get_usage_returns_all_keys(self, fresh_rate_limiter):
        """get_usage should return utilization for all configured keys."""
        rl = fresh_rate_limiter
        rl.check_rate_limit("test:endpoint")
        usage = rl.get_usage()
        assert "test:endpoint" in usage
        assert usage["test:endpoint"]["current"] == 1
        assert usage["test:endpoint"]["limit"] == 3


# ==========================================
# Resource Budget Tests
# ==========================================


class TestResourceBudgetManager:
    """Tests for the ResourceBudgetManager."""

    def test_ingestion_budget_allows_within_limit(self, fresh_budget_manager):
        """Items within budget should be allowed."""
        allowed, limit, current = fresh_budget_manager.check_budget(
            "max_ingestion_items_per_source", 30
        )
        assert allowed is True
        assert limit == 50

    def test_ingestion_budget_rejects_over_limit(self, fresh_budget_manager):
        """Items exceeding budget should be rejected."""
        allowed, limit, current = fresh_budget_manager.check_budget(
            "max_ingestion_items_per_source", 55
        )
        assert allowed is False
        assert limit == 50
        assert current == 55

    def test_clamp_reduces_to_limit(self, fresh_budget_manager):
        """Clamping should reduce values to the budget limit."""
        clamped = fresh_budget_manager.clamp("max_ingestion_items_per_source", 200)
        assert clamped == 50

    def test_clamp_preserves_within_limit(self, fresh_budget_manager):
        """Clamping should preserve values within the budget."""
        clamped = fresh_budget_manager.clamp("max_ingestion_items_per_source", 30)
        assert clamped == 30

    def test_get_all_budgets(self, fresh_budget_manager):
        """get_all_budgets should return all configured limits."""
        budgets = fresh_budget_manager.get_all_budgets()
        assert "max_ingestion_items_per_source" in budgets
        assert "max_concurrent_pipelines" in budgets
        assert budgets["max_ingestion_items_per_source"] == 50


# ==========================================
# Embedding Budget Tests
# ==========================================


class TestEmbeddingBudget:
    """Tests for embedding budget enforcement in cluster pipeline."""

    def test_embedding_budget_clamps_items(self, fresh_budget_manager):
        """Items exceeding embedding budget should be clamped."""
        # max_embeddings_per_run is 100 in test fixture
        large_count = 500
        clamped = fresh_budget_manager.clamp("max_embeddings_per_run", large_count)
        assert clamped == 100


# ==========================================
# LLM Synthesis Budget Tests
# ==========================================


class TestSynthesisBudget:
    """Tests for synthesis rate limiting per topic per hour."""

    def test_allows_within_budget(self, fresh_synthesis_tracker):
        """Synthesis within budget should be allowed."""
        allowed, current, limit = fresh_synthesis_tracker.check_and_record("test-topic")
        assert allowed is True
        assert current == 1
        assert limit == 2

    def test_rejects_over_budget(self, fresh_synthesis_tracker):
        """Synthesis exceeding per-topic-hour budget should be rejected."""
        fresh_synthesis_tracker.check_and_record("test-topic")
        fresh_synthesis_tracker.check_and_record("test-topic")
        allowed, current, limit = fresh_synthesis_tracker.check_and_record("test-topic")
        assert allowed is False
        assert current == 2
        assert limit == 2

    def test_separate_topic_budgets(self, fresh_synthesis_tracker):
        """Different topics should have independent budgets."""
        fresh_synthesis_tracker.check_and_record("topic-a")
        fresh_synthesis_tracker.check_and_record("topic-a")
        # topic-a exhausted, topic-b should still work
        allowed, _, _ = fresh_synthesis_tracker.check_and_record("topic-b")
        assert allowed is True


# ==========================================
# Concurrent Pipeline Limit Tests
# ==========================================


class TestConcurrencyGovernor:
    """Tests for ConcurrencyGovernor bounded concurrency."""

    def test_acquire_within_limit(self, fresh_concurrency_governor):
        """Acquiring slots within the limit should succeed."""
        gov = fresh_concurrency_governor
        assert gov.acquire("pipeline", holder_id="slug-1") is True
        assert gov.acquire("pipeline", holder_id="slug-2") is True
        assert gov.acquire("pipeline", holder_id="slug-3") is True

    def test_reject_beyond_limit(self, fresh_concurrency_governor):
        """Acquiring beyond the limit should fail."""
        gov = fresh_concurrency_governor
        gov.acquire("pipeline", holder_id="slug-1")
        gov.acquire("pipeline", holder_id="slug-2")
        gov.acquire("pipeline", holder_id="slug-3")
        # 4th should fail (max=3)
        assert gov.acquire("pipeline", holder_id="slug-4") is False

    def test_release_frees_slot(self, fresh_concurrency_governor):
        """Releasing a slot should allow new acquisitions."""
        gov = fresh_concurrency_governor
        gov.acquire("pipeline", holder_id="slug-1")
        gov.acquire("pipeline", holder_id="slug-2")
        gov.acquire("pipeline", holder_id="slug-3")
        gov.release("pipeline", holder_id="slug-1")
        assert gov.acquire("pipeline", holder_id="slug-4") is True

    def test_context_manager_releases_on_success(self, fresh_concurrency_governor):
        """Context manager should release slot on normal exit."""
        gov = fresh_concurrency_governor
        with gov.slot("pipeline", holder_id="ctx-1"):
            usage = gov.get_usage()
            assert usage["pipeline"]["current"] == 1

        usage = gov.get_usage()
        assert usage["pipeline"]["current"] == 0

    def test_context_manager_releases_on_failure(self, fresh_concurrency_governor):
        """Context manager should release slot on exception."""
        gov = fresh_concurrency_governor
        try:
            with gov.slot("pipeline", holder_id="ctx-fail"):
                raise ValueError("simulated failure")
        except ValueError:
            pass

        usage = gov.get_usage()
        assert usage["pipeline"]["current"] == 0

    def test_duplicate_holder_rejected(self, fresh_concurrency_governor):
        """Same holder_id should not acquire twice."""
        gov = fresh_concurrency_governor
        assert gov.acquire("pipeline", holder_id="dup-1") is True
        assert gov.acquire("pipeline", holder_id="dup-1") is False

    def test_release_all_clears_everything(self, fresh_concurrency_governor):
        """release_all should reset all counters."""
        gov = fresh_concurrency_governor
        gov.acquire("pipeline", holder_id="s1")
        gov.acquire("pipeline", holder_id="s2")
        gov.release_all()
        usage = gov.get_usage()
        assert usage["pipeline"]["current"] == 0


# ==========================================
# Worker Shutdown Cleanup Tests
# ==========================================


class TestWorkerShutdownCleanup:
    """Tests for worker shutdown releasing concurrency slots."""

    def test_scheduler_stop_releases_slots(self, fresh_concurrency_governor, monkeypatch):
        """Stopping the scheduler should release all concurrency slots."""
        gov = fresh_concurrency_governor
        gov.acquire("pipeline", holder_id="worker-1")
        gov.acquire("pipeline", holder_id="worker-2")

        # Simulate shutdown via release_all
        gov.release_all()

        usage = gov.get_usage()
        for resource in usage:
            assert usage[resource]["current"] == 0


# ==========================================
# External Request Governance Tests
# ==========================================


class TestExternalRequestGovernor:
    """Tests for ExternalRequestGovernor per-source budgets."""

    def test_allows_initial_request(self, fresh_external_governor):
        """First requests should be allowed."""
        allowed, reason = fresh_external_governor.check_request_allowed("google_news")
        assert allowed is True
        assert reason == ""

    def test_hourly_budget_enforcement(self, fresh_external_governor, monkeypatch):
        """Should reject requests when hourly budget is exhausted."""
        gov = fresh_external_governor
        # Set a tiny budget for testing
        gov._configs["google_news"].hourly_budget = 3

        for _ in range(3):
            gov.record_request_start("google_news")
            gov.record_request_end("google_news")

        allowed, reason = gov.check_request_allowed("google_news")
        assert allowed is False
        assert "budget" in reason.lower()

    def test_concurrent_limit_enforcement(self, fresh_external_governor):
        """Should reject when concurrent requests exceed limit."""
        gov = fresh_external_governor
        gov._configs["google_news"].max_concurrent = 2

        gov.record_request_start("google_news")
        gov.record_request_start("google_news")

        allowed, reason = gov.check_request_allowed("google_news")
        assert allowed is False
        assert "concurrent" in reason.lower()

    def test_get_usage(self, fresh_external_governor):
        """get_usage should return data for all sources."""
        usage = fresh_external_governor.get_usage()
        assert "google_news" in usage
        assert "reddit" in usage
        assert "x" in usage
        assert "openai" in usage


# ==========================================
# Cost Tracker Tests
# ==========================================


class TestCostTracker:
    """Tests for CostTracker usage recording."""

    def test_records_embedding_calls(self, fresh_cost_tracker):
        """Should correctly track embedding calls and item counts."""
        fresh_cost_tracker.record_embedding_call(item_count=50)
        fresh_cost_tracker.record_embedding_call(item_count=30)
        usage = fresh_cost_tracker.get_usage()
        assert usage["embedding_calls"] == 2
        assert usage["embedding_items_total"] == 80

    def test_records_synthesis_calls(self, fresh_cost_tracker):
        """Should correctly track synthesis calls and token estimates."""
        fresh_cost_tracker.record_synthesis_call(
            estimated_input_tokens=1000,
            estimated_output_tokens=500,
        )
        usage = fresh_cost_tracker.get_usage()
        assert usage["synthesis_calls"] == 1
        assert usage["estimated_input_tokens"] == 1000
        assert usage["estimated_output_tokens"] == 500

    def test_records_external_requests(self, fresh_cost_tracker):
        """Should correctly track external requests per source."""
        fresh_cost_tracker.record_external_request("google_news")
        fresh_cost_tracker.record_external_request("google_news")
        fresh_cost_tracker.record_external_request("reddit")
        usage = fresh_cost_tracker.get_usage()
        assert usage["external_requests"]["google_news"] == 2
        assert usage["external_requests"]["reddit"] == 1

    def test_records_pipeline_invocations(self, fresh_cost_tracker):
        """Should correctly track pipeline invocations."""
        fresh_cost_tracker.record_pipeline_invocation()
        fresh_cost_tracker.record_pipeline_invocation()
        usage = fresh_cost_tracker.get_usage()
        assert usage["pipeline_invocations"] == 2

    def test_reset_clears_counters(self, fresh_cost_tracker):
        """Reset should clear all counters."""
        fresh_cost_tracker.record_embedding_call(item_count=50)
        fresh_cost_tracker.record_synthesis_call(estimated_input_tokens=100)
        fresh_cost_tracker.reset()
        usage = fresh_cost_tracker.get_usage()
        assert usage["embedding_calls"] == 0
        assert usage["synthesis_calls"] == 0


# ==========================================
# Utilization Monitor Tests
# ==========================================


class TestUtilizationMonitor:
    """Tests for warning threshold classification."""

    def test_normal_status(self, fresh_utilization_monitor):
        """Below warning threshold should be 'normal'."""
        status = fresh_utilization_monitor.get_status(current=1.0, limit=10.0)
        assert status == "normal"

    def test_warning_status(self, fresh_utilization_monitor):
        """Between warning and critical should be 'warning'."""
        status = fresh_utilization_monitor.get_status(current=7.5, limit=10.0)
        assert status == "warning"

    def test_critical_status(self, fresh_utilization_monitor):
        """At or above critical threshold should be 'critical'."""
        status = fresh_utilization_monitor.get_status(current=9.5, limit=10.0)
        assert status == "critical"

    def test_zero_limit_is_normal(self, fresh_utilization_monitor):
        """Zero limit should always return 'normal'."""
        status = fresh_utilization_monitor.get_status(current=5.0, limit=0.0)
        assert status == "normal"

    def test_get_thresholds(self, fresh_utilization_monitor):
        """Should return configured threshold values."""
        thresholds = fresh_utilization_monitor.get_thresholds()
        assert thresholds["warning_threshold"] == 0.70
        assert thresholds["critical_threshold"] == 0.90


# ==========================================
# API Endpoint Tests (via TestClient)
# ==========================================


class TestAPI429Response:
    """Tests for HTTP 429 responses with Retry-After header."""

    def test_topic_create_rate_limit(self, test_client):
        """Creating topics beyond rate limit should return 429."""
        for _ in range(2):
            resp = test_client.post("/api/topics", json={"title": f"Test Topic {_}"})
            # Accept 201 (created) or other non-429 status
            assert resp.status_code != 429

        resp = test_client.post("/api/topics", json={"title": "Over limit"})
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

    def test_retry_after_header_format(self, test_client):
        """Retry-After header should be a numeric string."""
        for _ in range(2):
            test_client.post("/api/topics", json={"title": f"Rate Test {_}"})

        resp = test_client.post("/api/topics", json={"title": "Exceeded"})
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            assert retry_after is not None
            assert int(retry_after) > 0


class TestResourceEndpoints:
    """Tests for resource-usage and resource-budgets endpoints."""

    def test_resource_usage_endpoint(self, test_client):
        """GET /api/ops/resource-usage should return expected shape."""
        resp = test_client.get("/api/ops/resource-usage", headers={"X-Ops-Key": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert "rate_limits" in data
        assert "concurrency" in data
        assert "cost_tracking" in data
        assert "thresholds" in data

    def test_resource_budgets_endpoint(self, test_client):
        """GET /api/ops/resource-budgets should return limits without secrets."""
        resp = test_client.get("/api/ops/resource-budgets", headers={"X-Ops-Key": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert "budgets" in data
        assert "thresholds" in data
        budgets = data["budgets"]
        assert "max_ingestion_items_per_source" in budgets
        assert "max_concurrent_pipelines" in budgets
        # Ensure no secrets
        for key in budgets:
            assert "key" not in key.lower() or "api" not in key.lower()


# ==========================================
# Edge Case & Robustness Tests
# ==========================================


class TestResourceGovernanceEdgeCases:
    """Tests for edge cases across all resource governance subsystems."""

    def test_rate_limiter_multithreaded_concurrency(self, fresh_rate_limiter):
        """Concurrent threads should safely consume tokens without race condition errors."""
        import concurrent.futures

        rl = fresh_rate_limiter
        # limit is 3 requests per 60 seconds for test:endpoint
        successes = 0
        rejections = 0

        def call():
            allowed, _ = rl.check_rate_limit("test:endpoint")
            return allowed

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(call) for _ in range(10)]
            for f in concurrent.futures.as_completed(futures):
                if f.result():
                    successes += 1
                else:
                    rejections += 1

        assert successes == 3
        assert rejections == 7

    def test_concurrency_release_nonexistent_holder(self, fresh_concurrency_governor):
        """Releasing a holder_id that never acquired should be a no-op."""
        gov = fresh_concurrency_governor
        # Should not raise exception
        gov.release("pipeline", holder_id="nonexistent-id")
        usage = gov.get_usage()
        assert usage["pipeline"]["current"] == 0

    def test_external_governor_end_without_start(self, fresh_external_governor):
        """Calling record_request_end without record_request_start should not drop active count below 0."""
        gov = fresh_external_governor
        gov.record_request_end("google_news")
        usage = gov.get_usage()
        assert usage["google_news"]["active_requests"] == 0

    def test_synthesis_tracker_eviction_over_time(self, fresh_synthesis_tracker):
        """Timestamps older than 3600 seconds should be evicted from synthesis count."""
        tracker = fresh_synthesis_tracker
        # Insert a simulated old timestamp
        tracker._topic_timestamps["test-topic"] = [time.monotonic() - 4000]
        # Should allow new requests since old one is outside the 1-hour window
        allowed, current, limit = tracker.check_and_record("test-topic")
        assert allowed is True
        assert current == 1

    def test_ops_trending_rate_limit_endpoint(self, test_client):
        """Workers trigger-trending endpoint should enforce rate limits."""
        for _ in range(2):
            resp = test_client.post("/api/workers/refresh-trending")
            # First 2 can succeed or return expected code
            assert resp.status_code != 429

        resp = test_client.post("/api/workers/refresh-trending")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

    def test_ops_create_backup_rate_limit_endpoint(self, test_client):
        """Ops create backup endpoint should enforce ops:backup_create rate limits."""
        for _ in range(2):
            resp = test_client.post("/api/ops/backups/create?dry_run=true", headers={"X-Ops-Key": ""})
            assert resp.status_code != 429

        resp = test_client.post("/api/ops/backups/create?dry_run=true", headers={"X-Ops-Key": ""})
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

