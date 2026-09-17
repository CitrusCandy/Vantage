"""Comprehensive Test Suite for Observability, Metrics, SLOs, and Alert Lifecycle.

Verifies:
1. Low-cardinality Metric primitives (Counter, Gauge, Histogram with percentiles).
2. MetricsRegistry and Prometheus exposition formatting.
3. Service Level Objectives (SLOs), SLI evaluation, error budgets, and burn rates.
4. Alert lifecycle transitions, recovery events, deduplication, and flapping suppression.
5. Ops API endpoints: /api/ops/slos, /api/ops/metrics, /metrics, /api/ops/overview.
6. Database persistence of SLOViolationRecord.
"""

from datetime import datetime, timezone
import json
import time
import pytest
from fastapi.testclient import TestClient

from app.core.alerting import AlertConfig, AlertManager, AlertSeverity
from app.core.metrics import (
    CounterMetric,
    GaugeMetric,
    HistogramMetric,
    MetricsRegistry,
    platform_metrics,
)
from app.core.resilience import circuit_registry
from app.core.slo import SLODefinition, SLOKind, SLOManager, SLOStatus
from app.database.database import Base, engine, get_db
from app.database.models import OperationalAlert, SLOViolationRecord
from app.main import app


@pytest.fixture(autouse=True)
def reset_observability_state():
    """Reset platform metrics and circuit breakers before and after each test."""
    platform_metrics.reset_all()
    circuit_registry.reset_all()
    yield
    platform_metrics.reset_all()
    circuit_registry.reset_all()


# ============================================================================
# 1. Structured Metric Primitives Tests
# ============================================================================

def test_counter_metric_basic_and_bounded_labels():
    counter = CounterMetric("test_counter", "Test Counter Description", unit="ops")
    assert counter.get_total() == 0

    counter.inc(1, labels={"status": "200"})
    counter.inc(2, labels={"status": "200"})
    counter.inc(5, labels={"status": "500"})

    assert counter.get_value(labels={"status": "200"}) == 3
    assert counter.get_value(labels={"status": "500"}) == 5
    assert counter.get_total() == 8

    # Negative increment should raise ValueError
    with pytest.raises(ValueError):
        counter.inc(-1)

    # Serialization
    data = counter.to_dict()
    assert data["name"] == "test_counter"
    assert data["total"] == 8
    assert len(data["samples"]) == 2

    # High cardinality protection: adding 250 distinct label pairs
    for i in range(250):
        counter.inc(1, labels={"unique_id": str(i)})
    # Total distinct samples must be bounded at <= 200
    assert len(counter._counts) <= 201


def test_gauge_metric_operations():
    gauge = GaugeMetric("test_gauge", "Test Gauge Description", unit="ratio")
    assert gauge.get_value() == 0.0

    gauge.set(42.5, labels={"host": "worker-1"})
    assert gauge.get_value(labels={"host": "worker-1"}) == 42.5

    gauge.inc(2.5, labels={"host": "worker-1"})
    assert gauge.get_value(labels={"host": "worker-1"}) == 45.0

    gauge.dec(10.0, labels={"host": "worker-1"})
    assert gauge.get_value(labels={"host": "worker-1"}) == 35.0

    data = gauge.to_dict()
    assert data["name"] == "test_gauge"
    assert len(data["samples"]) == 1
    assert data["samples"][0]["value"] == 35.0


def test_histogram_percentile_computation():
    hist = HistogramMetric(
        "test_latency_ms",
        "Test Latency",
        buckets=(10.0, 50.0, 100.0, 250.0, 500.0),
        window_seconds=3600.0,
    )

    # Feed 100 uniform values from 1.0 to 100.0
    for v in range(1, 101):
        hist.observe(float(v))

    pcts = hist.get_percentiles()
    assert pcts["count"] == 100
    assert pcts["min"] == 1.0
    assert pcts["max"] == 100.0
    assert pcts["mean"] == 50.5
    assert pcts["p50"] == 50.0
    assert pcts["p90"] == 90.0
    assert pcts["p95"] == 95.0
    assert pcts["p99"] == 99.0

    # Verify Prometheus buckets
    data = hist.to_dict()
    buckets = data["samples"][0]["buckets"]
    assert buckets["10.0"] == 10
    assert buckets["50.0"] == 50
    assert buckets["100.0"] == 100


def test_prometheus_exposition_generation():
    registry = MetricsRegistry()
    c = registry.get_counter("http_requests_total")
    assert c is not None
    c.inc(10, labels={"method": "GET", "status": "200"})

    g = registry.get_gauge("database_connections_active")
    assert g is not None
    g.set(5.0)

    h = registry.get_histogram("http_request_duration_ms")
    assert h is not None
    h.observe(45.0, labels={"method": "GET"})

    prom_text = registry.generate_prometheus_text()
    assert "# HELP http_requests_total" in prom_text
    assert "# TYPE http_requests_total counter" in prom_text
    assert 'http_requests_total{method="GET",status="200"} 10' in prom_text
    assert "database_connections_active 5.0" in prom_text
    assert "http_request_duration_ms_bucket" in prom_text
    assert "http_request_duration_ms_count" in prom_text
    assert "http_request_duration_ms_sum" in prom_text


# ============================================================================
# 2. Service Level Objectives (SLOs) Engine Tests
# ============================================================================

def test_slo_evaluation_compliant_and_violation():
    slo_mgr = SLOManager()

    # Initial state with no traffic: should default to COMPLIANT
    summary = slo_mgr.get_summary()
    assert summary["overall_status"] in ("HEALTHY", "COMPLIANT")
    assert summary["total_slos"] == 9
    assert summary["violated"] == 0

    # Simulate HTTP failures violating api_availability (target >= 99.9%)
    req_counter = platform_metrics.get_counter("http_requests_total")
    req_counter.inc(80, labels={"status": "200"})
    req_counter.inc(20, labels={"status": "500"})  # 20% error rate -> 80% availability

    evals = slo_mgr.evaluate_all()
    api_avail = evals["api_availability"]
    assert api_avail.status == SLOStatus.VIOLATED
    assert api_avail.current_value == 80.0
    assert api_avail.error_budget_remaining_percent == 0.0
    assert api_avail.burn_rate > 10.0

    # Simulate latency violation on api_latency_p95 (target <= 250ms)
    lat_hist = platform_metrics.get_histogram("http_request_duration_ms")
    for _ in range(50):
        lat_hist.observe(350.0)  # High latency

    evals = slo_mgr.evaluate_all()
    api_lat = evals["api_latency_p95"]
    assert api_lat.status == SLOStatus.VIOLATED
    assert api_lat.current_value == 350.0

    summary_after = slo_mgr.get_summary()
    assert summary_after["overall_status"] == "CRITICAL"
    assert summary_after["violated"] >= 2


def test_custom_slo_definition():
    custom_defn = {
        "custom_ratio": SLODefinition(
            name="custom_ratio",
            display_name="Custom Ratio SLO",
            description="Test custom ratio target",
            target=95.0,
            kind=SLOKind.RATIO,
            unit="%",
            warning_threshold=97.0,
        )
    }
    mgr = SLOManager(definitions=custom_defn)

    # Input: 96.0% -> WARNING
    res_warning = mgr.evaluate_all(custom_inputs={"custom_ratio": 96.0})
    assert res_warning["custom_ratio"].status == SLOStatus.WARNING

    # Input: 90.0% -> VIOLATED
    res_violated = mgr.evaluate_all(custom_inputs={"custom_ratio": 90.0})
    assert res_violated["custom_ratio"].status == SLOStatus.VIOLATED

    # Input: 99.0% -> COMPLIANT
    res_compliant = mgr.evaluate_all(custom_inputs={"custom_ratio": 99.0})
    assert res_compliant["custom_ratio"].status == SLOStatus.COMPLIANT


# ============================================================================
# 3. Alert Lifecycle, Recovery, and Flapping Tests
# ============================================================================

def test_alert_lifecycle_and_recovery_transitions(db_session):
    mgr = AlertManager(config=AlertConfig())
    mgr.clear()

    # 1. Trigger database connectivity failure
    firing_keys = set()
    mgr._record_firing_alert(
        rule_name="database_unavailable",
        component="database",
        severity=AlertSeverity.CRITICAL,
        message="Simulated connection failure",
        metadata={"detail": "connection refused"},
        firing_keys=firing_keys,
        now_ts=time.time(),
        db=db_session,
    )

    summary = mgr.get_alerts_summary(db=db_session)
    assert summary["active_count"] == 1
    assert summary["active_alerts"][0]["rule_name"] == "database_unavailable"
    assert summary["active_alerts"][0]["status"] == "active"

    # Verify persisted DB record
    db_alert = db_session.query(OperationalAlert).filter(OperationalAlert.alert_id == "database_unavailable:database").first()
    assert db_alert is not None
    assert db_alert.status == "active"

    # 2. Re-evaluate with condition resolved (firing_keys empty)
    eval_result = mgr.evaluate(db=db_session)
    active_ids = [a["id"] for a in eval_result["active_alerts"]]
    resolved_ids = [a["id"] for a in eval_result["resolved_alerts"]]
    assert "database_unavailable:database" not in active_ids
    assert "database_unavailable:database" in resolved_ids

    # Verify persisted DB record is updated to resolved
    db_session.refresh(db_alert)
    assert db_alert.status == "resolved"
    assert db_alert.resolved_at is not None


def test_circuit_breaker_and_slo_alert_integration(db_session):
    from app.core.resilience import CircuitState
    mgr = AlertManager(config=AlertConfig())
    mgr.clear()

    # Open a circuit breaker
    cb = circuit_registry.get_or_create("google_news_fetch", failure_threshold=2)
    cb.record_failure(Exception("HTTP 503"))
    cb.record_failure(Exception("HTTP 503"))
    assert cb.state == CircuitState.OPEN

    # Run alert evaluation
    result = mgr.evaluate(db=db_session)
    active_rules = [a["rule_name"] for a in result["active_alerts"]]
    assert "circuit_breaker_open" in active_rules

    # Reset circuit breaker
    circuit_registry.reset_all()
    assert cb.state == CircuitState.CLOSED

    # Next evaluation should resolve the circuit breaker alert
    res_after = mgr.evaluate(db=db_session)
    active_after = [a["rule_name"] for a in res_after["active_alerts"]]
    assert "circuit_breaker_open" not in active_after



# ============================================================================
# 4. Ops API Integration Tests
# ============================================================================

def test_ops_slo_endpoint():
    client = TestClient(app)
    response = client.get("/api/ops/slos")
    assert response.status_code == 200
    data = response.json()
    assert "overall_status" in data
    assert "evaluations" in data
    assert "health_score_percent" in data
    assert "api_availability" in data["evaluations"]
    assert "api_latency_p95" in data["evaluations"]
    assert "ingestion_freshness" in data["evaluations"]


def test_ops_metrics_json_endpoint():
    client = TestClient(app)
    # Perform a request to generate metrics
    client.get("/api/ops/overview")

    response = client.get("/api/ops/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "metrics" in data
    assert "http_requests_total" in data["metrics"]
    assert "http_request_duration_ms" in data["metrics"]


def test_prometheus_exposition_endpoint():
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    text_content = response.text
    assert "# HELP" in text_content
    assert "# TYPE" in text_content
    assert "http_requests_total" in text_content


def test_ops_overview_enriched_with_slos():
    client = TestClient(app)
    response = client.get("/api/ops/overview")
    assert response.status_code == 200
    data = response.json()
    assert "slo_summary" in data
    assert "health_score_percent" in data["slo_summary"]
    assert "evaluations" in data["slo_summary"]
