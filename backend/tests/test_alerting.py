from datetime import datetime, timedelta
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.alerting import AlertConfig, AlertInstance, AlertManager, AlertSeverity, alert_manager
from app.core.telemetry import SourceHealthStatus, ops_metrics
from app.database.database import Base, get_db
from app.main import app
from app.workers.scheduler import scheduler


class TestProductionAlertingEngine(unittest.TestCase):
    """Deterministic tests for alert evaluation, deduplication, cooldown, resolution, and security."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.Session()

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

        # Reset singleton state before each test
        alert_manager.clear()
        ops_metrics.recent_runs.clear()
        from app.core.metrics import platform_metrics
        from app.core.resilience import circuit_registry
        platform_metrics.reset_all()
        circuit_registry.reset_all()
        for src in ops_metrics.source_health.values():
            src.success_count = 0
            src.failure_count = 0
            src.timeout_count = 0
            src.total_latency_ms = 0.0
            src.last_success = None
            src.last_failure = None
            src.last_error_summary = None
            src.enabled = True

    def tearDown(self):
        alert_manager.clear()
        from app.core.metrics import platform_metrics
        from app.core.resilience import circuit_registry
        platform_metrics.reset_all()
        circuit_registry.reset_all()
        app.dependency_overrides.clear()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_database_unavailable_and_latency_alerts(self):
        """Verify database connectivity failures and high latency triggers appropriate alerts."""
        # 1. Normal DB check should be nominal
        res_nominal = alert_manager.evaluate(db=self.db)
        self.assertEqual(len(res_nominal["active_alerts"]), 1)  # worker_stopped is default inactive in test

        # 2. Mock DB failure
        class BrokenSession:
            def execute(self, *args, **kwargs):
                raise RuntimeError("Connection to Postgres timed out")

        res_db_fail = alert_manager.evaluate(db=BrokenSession())
        active_rules = {a["rule_name"]: a for a in res_db_fail["active_alerts"]}
        self.assertIn("database_unavailable", active_rules)
        self.assertEqual(active_rules["database_unavailable"]["severity"], "critical")
        self.assertIn("Postgres timed out", active_rules["database_unavailable"]["message"])

        # 3. Test high latency threshold
        with patch.object(AlertConfig, "db_latency_threshold_ms", 0.0001):
            res_latency = alert_manager.evaluate(db=self.db)
            rules = {a["rule_name"]: a for a in res_latency["active_alerts"]}
            self.assertIn("database_high_latency", rules)
            self.assertEqual(rules["database_high_latency"]["severity"], "warning")

    def test_worker_stopped_and_stale_detection(self):
        """Verify worker stoppage, critical error, and stale cycle detection."""
        # 1. Stopped worker scheduler
        res = alert_manager.evaluate(db=self.db)
        rules = {a["rule_name"]: a for a in res["active_alerts"]}
        self.assertIn("worker_stopped", rules)

        # 2. Worker with critical crash error
        with patch.object(scheduler, "get_status", return_value={"is_running": False, "last_error": "Fatal worker thread crash"}):
            res_error = alert_manager.evaluate(db=self.db)
            rules_err = {a["rule_name"]: a for a in res_error["active_alerts"]}
            self.assertIn("worker_stopped", rules_err)
            self.assertEqual(rules_err["worker_stopped"]["severity"], "critical")

        # 3. Worker running but stale (last cycle older than interval + grace period)
        old_time = (datetime.utcnow() - timedelta(hours=5)).isoformat()
        with patch.object(
            scheduler,
            "get_status",
            return_value={
                "is_running": True,
                "last_run_time": old_time,
                "worker_interval_hours": 1,
                "last_error": None,
            },
        ):
            res_stale = alert_manager.evaluate(db=self.db)
            rules_stale = {a["rule_name"]: a for a in res_stale["active_alerts"]}
            self.assertIn("worker_stale", rules_stale)
            self.assertEqual(rules_stale["worker_stale"]["severity"], "warning")

    def test_source_failure_spike_and_disabled_source_behavior(self):
        """Verify source failure alerts fire for enabled sources and are suppressed for disabled sources."""
        # Record 5 failures on google_news
        for _ in range(5):
            ops_metrics.record_source_execution("google_news", success=False, latency_ms=10.0, error_summary="HTTP 503")

        res = alert_manager.evaluate(db=self.db)
        rules = {a["rule_name"]: a for a in res["active_alerts"]}
        self.assertIn("source_failure_spike", rules)
        self.assertEqual(rules["source_failure_spike"]["component"], "source:google_news")
        self.assertEqual(rules["source_failure_spike"]["severity"], "critical")

        # Disable source: should not generate false alarms
        ops_metrics.source_health["google_news"].enabled = False
        res_disabled = alert_manager.evaluate(db=self.db)
        rules_disabled = {a["rule_name"]: a for a in res_disabled["active_alerts"]}
        self.assertNotIn("source_failure_spike", rules_disabled)

    def test_repeated_llm_and_x_scraper_failures(self):
        """Verify dedicated alert rules for LLM perspective synthesis and X scraper."""
        # 1. LLM failures
        for _ in range(3):
            ops_metrics.record_source_execution("openai", success=False, latency_ms=100.0, error_summary="Rate limit exceeded")

        res = alert_manager.evaluate(db=self.db)
        rules = {a["rule_name"]: a for a in res["active_alerts"]}
        self.assertIn("llm_repeated_failures", rules)
        self.assertEqual(rules["llm_repeated_failures"]["severity"], "critical")

        # 2. X scraper failures
        for _ in range(4):
            ops_metrics.record_source_execution("x", success=False, latency_ms=500.0, error_summary="Selenium timeout")

        res_x = alert_manager.evaluate(db=self.db)
        rules_x = {a["rule_name"]: a for a in res_x["active_alerts"]}
        self.assertIn("x_scraper_repeated_failures", rules_x)
        self.assertEqual(rules_x["x_scraper_repeated_failures"]["severity"], "warning")

    def test_pipeline_failure_spike_and_high_latency(self):
        """Verify pipeline failure spikes and unusually high latency trigger alerts."""
        # 1. Record 3 pipeline failures
        for i in range(3):
            ops_metrics.record_pipeline_run(
                pipeline_name="test_pipeline",
                topic_slug=f"topic-{i}",
                total_duration_ms=120.0,
                stages_ms={"ingest": 50.0},
                status="failed",
                error="Stage execution aborted",
            )

        res = alert_manager.evaluate(db=self.db)
        rules = {a["rule_name"]: a for a in res["active_alerts"]}
        self.assertIn("pipeline_failure_spike", rules)
        self.assertEqual(rules["pipeline_failure_spike"]["severity"], "critical")

        # 2. High latency alert
        ops_metrics.recent_runs.clear()
        ops_metrics.record_pipeline_run(
            pipeline_name="slow_pipeline",
            topic_slug="slow-topic",
            total_duration_ms=45000.0,  # 45 seconds > 30s threshold
            stages_ms={"clustering": 40000.0},
            status="success",
        )

        res_lat = alert_manager.evaluate(db=self.db)
        rules_lat = {a["rule_name"]: a for a in res_lat["active_alerts"]}
        self.assertIn("pipeline_high_latency", rules_lat)
        self.assertEqual(rules_lat["pipeline_high_latency"]["severity"], "warning")

    def test_stale_source_detection(self):
        """Verify that an enabled source with last_success older than threshold triggers source_stale."""
        old_success = (datetime.utcnow() - timedelta(hours=30)).isoformat()
        ops_metrics.source_health["reddit"].last_success = old_success

        with patch.object(AlertConfig, "source_stale_hours", 24.0):
            res = alert_manager.evaluate(db=self.db)
            rules = {a["rule_name"]: a for a in res["active_alerts"]}
            self.assertIn("source_stale", rules)
            self.assertEqual(rules["source_stale"]["component"], "source:reddit")

    def test_alert_deduplication_cooldown_and_resolution_lifecycle(self):
        """Verify alert deduplication increments occurrence count and resolves when healthy."""
        # 1. Trigger failure
        for _ in range(4):
            ops_metrics.record_source_execution("reddit", success=False, latency_ms=20.0)

        # First evaluation: alert created with occurrence_count = 1
        res1 = alert_manager.evaluate(db=self.db)
        reddit_alert_1 = next(a for a in res1["active_alerts"] if a["component"] == "source:reddit")
        self.assertEqual(reddit_alert_1["occurrence_count"], 1)

        # Second evaluation while still failing: deduplicated, occurrence_count = 2, same ID
        res2 = alert_manager.evaluate(db=self.db)
        reddit_alert_2 = next(a for a in res2["active_alerts"] if a["component"] == "source:reddit")
        self.assertEqual(reddit_alert_2["occurrence_count"], 2)
        self.assertEqual(reddit_alert_1["id"], reddit_alert_2["id"])
        self.assertEqual(len(res2["active_alerts"]), len(res1["active_alerts"]))

        # 3. Resolve failure: reset failures and add successes
        ops_metrics.source_health["reddit"].failure_count = 0
        ops_metrics.source_health["reddit"].success_count = 10
        ops_metrics.source_health["reddit"].last_success = datetime.utcnow().isoformat()

        res3 = alert_manager.evaluate(db=self.db)
        active_components = [a["component"] for a in res3["active_alerts"]]
        self.assertNotIn("source:reddit", active_components)
        self.assertGreaterEqual(res3["resolved_count"], 1)

        resolved_reddit = next(a for a in res3["resolved_alerts"] if a["component"] == "source:reddit")
        self.assertEqual(resolved_reddit["status"], "resolved")
        self.assertIsNotNone(resolved_reddit["resolved_at"])

    def test_alert_api_endpoints_and_security_guard(self):
        """Verify GET /api/ops/alerts and protected POST /api/ops/alerts/evaluate endpoints."""
        # 1. GET /api/ops/alerts
        resp = self.client.get("/api/ops/alerts")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("active_count", data)
        self.assertIn("resolved_count", data)
        self.assertIn("active_alerts", data)
        self.assertIn("resolved_alerts", data)

        # 2. POST /api/ops/alerts/evaluate (unprotected default)
        resp_eval = self.client.post("/api/ops/alerts/evaluate")
        self.assertEqual(resp_eval.status_code, 200)
        data_eval = resp_eval.json()
        self.assertEqual(data_eval["status"], "evaluated")

        # 3. Test OPS_API_KEY security guard
        orig_key = os.environ.get("OPS_API_KEY")
        try:
            os.environ["OPS_API_KEY"] = "super-alert-ops-key"

            # Rejection without header
            resp_unauth = self.client.post("/api/ops/alerts/evaluate")
            self.assertEqual(resp_unauth.status_code, 401)

            # Rejection with invalid header
            resp_invalid = self.client.post("/api/ops/alerts/evaluate", headers={"X-Ops-Key": "wrong"})
            self.assertEqual(resp_invalid.status_code, 401)

            # Authorization with valid header
            resp_auth = self.client.post("/api/ops/alerts/evaluate", headers={"X-Ops-Key": "super-alert-ops-key"})
            self.assertEqual(resp_auth.status_code, 200)
        finally:
            if orig_key is not None:
                os.environ["OPS_API_KEY"] = orig_key
            else:
                os.environ.pop("OPS_API_KEY", None)

    def test_no_credential_leakage_in_alert_payloads(self):
        """Verify secrets, tokens, passwords, and DB credentials are sanitized from alerts."""
        fake_secret_meta = {
            "token": "sk-proj-supersecretkey1234567890abcdefghij",
            "auth": "Bearer secret_bearer_token_xyz987654321",
            "db_conn": "postgresql://vantage_user:topsecretpass@db.internal:5432/vantage",
        }
        res = alert_manager._record_firing_alert(
            rule_name="test_rule",
            component="test_component",
            severity=AlertSeverity.INFO,
            message="Alert message containing sk-proj-1234567890abcdefghijk secret",
            metadata=fake_secret_meta,
            firing_keys=set(),
        )

        summary = alert_manager.get_alerts_summary()
        dumped = str(summary).lower()
        self.assertNotIn("topsecretpass", dumped)
        self.assertNotIn("supersecretkey", dumped)
        self.assertNotIn("secret_bearer_token", dumped)
