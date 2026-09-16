import os
from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.core.alerting import AlertManager, AlertSeverity
from app.core.telemetry import OpsMetricsRegistry
from app.database.database import Base, get_db
from app.database.models import (
    OperationalAlert,
    PipelineRun,
    SourceExecution,
    Topic,
    WorkerCycle,
)
from app.database.cleanup_ops_history import cleanup_ops_history
from app.main import app
from app.workers.scheduler import BackgroundScheduler, WorkerConfig


from sqlalchemy.pool import StaticPool


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(test_db):
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def test_pipeline_run_persistence(test_db, monkeypatch):
    """Ensure pipeline runs automatically persist to PostgreSQL / DB models."""
    monkeypatch.setattr("app.database.database.SessionLocal", lambda: test_db)

    metrics = OpsMetricsRegistry()
    metrics.record_pipeline_run(
        pipeline_name="test_discourse_pipeline",
        topic_slug="ai-regulation",
        total_duration_ms=452.1,
        stages_ms={"ingestion": 120.0, "clustering": 150.0, "synthesis": 182.1},
        status="success",
        sample_size=42,
        cluster_count=4,
        perspective_count=3,
    )

    persisted = test_db.query(PipelineRun).first()
    assert persisted is not None
    assert persisted.pipeline_type == "test_discourse_pipeline"
    assert persisted.topic_slug == "ai-regulation"
    assert persisted.status == "success"
    assert persisted.sample_size == 42
    assert persisted.cluster_count == 4
    assert persisted.perspective_count == 3
    assert persisted.duration_ms == 452.1
    assert persisted.stages_ms["clustering"] == 150.0


def test_source_execution_persistence(test_db, monkeypatch):
    """Ensure scraper and source execution metrics persist operational metadata."""
    monkeypatch.setattr("app.database.database.SessionLocal", lambda: test_db)

    metrics = OpsMetricsRegistry()
    metrics.record_source_execution(
        source_name="google_news",
        success=True,
        latency_ms=185.5,
        operation="fetch",
        item_count=35,
    )

    metrics.record_source_execution(
        source_name="x",
        success=False,
        latency_ms=3000.0,
        is_timeout=True,
        error_summary="API Key rate limited: bearer secret_token_xyz",
        operation="scrape",
        item_count=0,
    )

    executions = test_db.query(SourceExecution).order_by(SourceExecution.execution_id.asc()).all()
    assert len(executions) == 2

    # First execution: success
    assert executions[0].source == "google_news"
    assert executions[0].status == "success"
    assert executions[0].item_count == 35
    assert executions[0].duration_ms == 185.5

    # Second execution: timeout and masked secret
    assert executions[1].source == "x"
    assert executions[1].status == "timeout"
    assert executions[1].item_count == 0
    assert "secret_token_xyz" not in (executions[1].error_type or "")
    assert "[REDACTED]" in (executions[1].error_type or "")


def test_worker_cycle_persistence(test_db, monkeypatch):
    """Ensure worker cycle runs are recorded and persisted."""
    monkeypatch.setattr("app.database.database.SessionLocal", lambda: test_db)

    worker = BackgroundScheduler(config=WorkerConfig(worker_interval_hours=1))
    worker._persist_cycle(
        started_at=datetime.utcnow() - timedelta(seconds=12),
        completed_at=datetime.utcnow(),
        duration_ms=12000.0,
        topics_considered=15,
        topics_refreshed=5,
        topics_skipped=9,
        topics_failed=1,
        status="success",
        error_summary=None,
    )

    cycle = test_db.query(WorkerCycle).first()
    assert cycle is not None
    assert cycle.duration_ms == 12000.0
    assert cycle.topics_considered == 15
    assert cycle.topics_refreshed == 5
    assert cycle.topics_skipped == 9
    assert cycle.topics_failed == 1
    assert cycle.status == "success"


def test_alert_persistence_and_deduplication(test_db, monkeypatch):
    """Ensure alerts persist to DB, occurrence counts increment, and resolution updates status."""
    monkeypatch.setattr("app.database.database.SessionLocal", lambda: test_db)

    mgr = AlertManager()
    mgr.clear()

    # 1. Fire an alert
    firing_keys = set()
    mgr._record_firing_alert(
        rule_name="test_alert_rule",
        component="test_component",
        severity=AlertSeverity.WARNING,
        message="Initial test alert with api_key=supersecret123",
        metadata={"detail": "first_occurrence"},
        firing_keys=firing_keys,
        db=test_db,
    )

    db_alert = test_db.query(OperationalAlert).filter(OperationalAlert.alert_id == "test_alert_rule:test_component").first()
    assert db_alert is not None
    assert db_alert.status == "active"
    assert db_alert.occurrence_count == 1
    assert "supersecret123" not in db_alert.message
    assert "[REDACTED]" in db_alert.message

    # 2. Fire the same alert again -> occurrence_count should increment in DB
    mgr._record_firing_alert(
        rule_name="test_alert_rule",
        component="test_component",
        severity=AlertSeverity.WARNING,
        message="Second occurrence",
        metadata={"detail": "second_occurrence"},
        firing_keys=firing_keys,
        db=test_db,
    )

    db_alert_updated = test_db.query(OperationalAlert).filter(OperationalAlert.alert_id == "test_alert_rule:test_component").first()
    assert db_alert_updated.occurrence_count == 2
    assert db_alert_updated.status == "active"

    # 3. Simulate evaluation cycle where alert is no longer firing -> should resolve
    test_db.commit()
    # Alert resolves when not in firing keys
    mgr.evaluate(db=test_db)
    db_alert_resolved = test_db.query(OperationalAlert).filter(OperationalAlert.alert_id == "test_alert_rule:test_component").first()
    assert db_alert_resolved.status == "resolved"
    assert db_alert_resolved.resolved_at is not None


def test_alert_restart_reload_and_deduplication(test_db, monkeypatch):
    """Ensure that on server restart, active alerts are loaded from DB so counts continue incrementing."""
    monkeypatch.setattr("app.database.database.SessionLocal", lambda: test_db)

    # Insert an existing active alert into DB
    existing = OperationalAlert(
        alert_id="database_high_latency:database",
        alert_type="database_high_latency",
        severity="warning",
        component="database",
        status="active",
        message="Database ping latency high",
        occurrence_count=5,
        first_seen=datetime.utcnow() - timedelta(hours=2),
        last_seen=datetime.utcnow() - timedelta(minutes=5),
    )
    test_db.add(existing)
    test_db.commit()

    # Create a fresh AlertManager simulating a new process start
    fresh_mgr = AlertManager()
    fresh_mgr.active_alerts.clear()
    fresh_mgr.resolved_history.clear()
    fresh_mgr._loaded_from_db = False

    # Check alert summary; should load from DB
    summary = fresh_mgr.get_alerts_summary(db=test_db)
    assert summary["active_count"] == 1
    active = summary["active_alerts"][0]
    assert active["id"] == "database_high_latency:database"
    assert active["occurrence_count"] == 5

    # Fire again in the fresh manager -> occurrence_count should become 6
    firing_keys = set()
    fresh_mgr._record_firing_alert(
        rule_name="database_high_latency",
        component="database",
        severity=AlertSeverity.WARNING,
        message="Database ping latency high again",
        metadata={},
        firing_keys=firing_keys,
        db=test_db,
    )

    db_updated = test_db.query(OperationalAlert).filter(OperationalAlert.alert_id == "database_high_latency:database").first()
    assert db_updated.occurrence_count == 6


def test_history_endpoint_filtering_and_pagination(client, test_db):
    """Test GET /api/ops/history with type filtering, component filtering, and pagination."""
    # Seed 10 pipeline runs
    for i in range(10):
        test_db.add(
            PipelineRun(
                topic_slug=f"topic-{i}",
                pipeline_type="discourse_pipeline",
                status="success" if i % 2 == 0 else "failed",
                started_at=datetime.utcnow() - timedelta(minutes=10 - i),
                duration_ms=100.0 * (i + 1),
                sample_size=10 + i,
            )
        )
    # Seed 5 source executions
    for i in range(5):
        test_db.add(
            SourceExecution(
                source="reddit" if i % 2 == 0 else "x",
                operation="fetch",
                status="success" if i != 1 else "timeout",
                started_at=datetime.utcnow() - timedelta(minutes=5 - i),
                duration_ms=200.0,
                item_count=15,
            )
        )
    # Seed 3 worker cycles
    for i in range(3):
        test_db.add(
            WorkerCycle(
                started_at=datetime.utcnow() - timedelta(hours=i + 1),
                duration_ms=5000.0,
                topics_considered=10,
                topics_refreshed=2,
                status="success",
            )
        )
    # Seed 2 alerts
    test_db.add(
        OperationalAlert(
            alert_id="test:comp1",
            alert_type="test",
            severity="warning",
            component="comp1",
            status="active",
            message="Alert 1",
            occurrence_count=1,
            first_seen=datetime.utcnow(),
            last_seen=datetime.utcnow(),
        )
    )
    test_db.commit()

    # 1. Test All items with pagination
    res = client.get("/api/ops/history?type=all&limit=5&page=1")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["total_count"] >= 18
    assert len(data["items"]) == 5
    assert data["has_next"] is True

    # 2. Test Filtering by Pipeline Runs only
    res_pipes = client.get("/api/ops/history?type=pipeline_runs&status=failed")
    assert res_pipes.status_code == 200
    pipes_data = res_pipes.json()
    assert pipes_data["type"] == "pipeline_runs"
    assert pipes_data["total_count"] == 5
    for item in pipes_data["items"]:
        assert item["status"] == "failed"
        assert item["record_type"] == "pipeline_run"

    # 3. Test Filtering by Source Executions with component
    res_sources = client.get("/api/ops/history?type=source_executions&component=reddit")
    assert res_sources.status_code == 200
    src_data = res_sources.json()
    assert src_data["type"] == "source_executions"
    for item in src_data["items"]:
        assert item["source"] == "reddit"

    # 4. Test Filtering by Worker Cycles
    res_workers = client.get("/api/ops/history?type=worker_cycles")
    assert res_workers.status_code == 200
    worker_data = res_workers.json()
    assert worker_data["total_count"] == 3

    # 5. Test Filtering by Alerts
    res_alerts = client.get("/api/ops/history?type=alerts&status=active")
    assert res_alerts.status_code == 200
    alerts_data = res_alerts.json()
    assert alerts_data["total_count"] >= 1


def test_retention_cleanup(test_db, monkeypatch):
    """Test cleanup_ops_history prunes records older than threshold while preserving active alerts."""
    old_date = datetime.utcnow() - timedelta(days=40)
    recent_date = datetime.utcnow() - timedelta(days=5)

    # Add old and recent pipeline runs
    test_db.add(PipelineRun(pipeline_type="old", status="success", started_at=old_date, duration_ms=100))
    test_db.add(PipelineRun(pipeline_type="recent", status="success", started_at=recent_date, duration_ms=100))

    # Add old and recent source executions
    test_db.add(SourceExecution(source="reddit", status="success", started_at=old_date, duration_ms=100))
    test_db.add(SourceExecution(source="reddit", status="success", started_at=recent_date, duration_ms=100))

    # Add old active alert and old resolved alert
    test_db.add(
        OperationalAlert(
            alert_id="active:old",
            alert_type="active_test",
            severity="critical",
            component="core",
            status="active",
            message="Never delete active",
            occurrence_count=1,
            first_seen=old_date,
            last_seen=old_date,
        )
    )
    test_db.add(
        OperationalAlert(
            alert_id="resolved:old",
            alert_type="resolved_test",
            severity="warning",
            component="core",
            status="resolved",
            message="Old resolved alert",
            occurrence_count=1,
            first_seen=old_date,
            last_seen=old_date,
            resolved_at=old_date,
        )
    )
    test_db.commit()

    # Dry-run check
    dry_res = cleanup_ops_history(ops_retention_days=30, alert_retention_days=30, dry_run=True, db=test_db)
    assert dry_res["dry_run"] is True
    assert dry_res["total_pruned"] == 3  # 1 old pipe + 1 old src + 1 old resolved alert

    # Execute cleanup
    cleanup_res = cleanup_ops_history(ops_retention_days=30, alert_retention_days=30, dry_run=False, db=test_db)
    assert cleanup_res["pipeline_runs_pruned"] == 1
    assert cleanup_res["source_executions_pruned"] == 1
    assert cleanup_res["resolved_alerts_pruned"] == 1
    assert cleanup_res["total_pruned"] == 3

    # Active alert must still exist
    active_alert = test_db.query(OperationalAlert).filter(OperationalAlert.alert_id == "active:old").first()
    assert active_alert is not None
    assert active_alert.status == "active"

    # Recent records must still exist
    recent_pipe = test_db.query(PipelineRun).filter(PipelineRun.pipeline_type == "recent").first()
    assert recent_pipe is not None


def test_persistence_failure_isolation(test_db, monkeypatch):
    """Ensure DB persistence failures in telemetry and workers do not disrupt the caller."""
    def broken_session():
        raise RuntimeError("Simulated DB connection failure")

    monkeypatch.setattr("app.database.database.SessionLocal", broken_session)

    metrics = OpsMetricsRegistry()
    # Must not throw
    metrics.record_pipeline_run(
        pipeline_name="test_pipeline",
        topic_slug="topic-x",
        total_duration_ms=150.0,
        stages_ms={},
        status="success",
    )
    metrics.record_source_execution(
        source_name="google_news",
        success=True,
        latency_ms=100.0,
    )

    worker = BackgroundScheduler()
    # Must not throw
    worker._persist_cycle(
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        duration_ms=100.0,
    )
