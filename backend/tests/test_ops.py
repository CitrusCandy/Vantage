from datetime import datetime
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.telemetry import ops_metrics
from app.database.database import Base, get_db
from app.database.models import Topic
from app.main import app


class TestOperationsAndObservabilityAPIs(unittest.TestCase):
    """Test operational monitoring APIs, source health metrics, and protected operational controls."""

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

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_get_ops_overview(self):
        # Create a sample topic
        topic = Topic(
            title="Ops Test Topic",
            slug="ops-test-topic",
            trending_score=0.85,
            updated_at=datetime.utcnow(),
        )
        self.db.add(topic)
        self.db.commit()

        response = self.client.get("/api/ops/overview")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn(data["status"], ("healthy", "degraded", "unavailable"))
        self.assertIn("api", data)
        self.assertEqual(data["api"]["status"], "healthy")
        self.assertIn("database", data)
        self.assertEqual(data["database"]["status"], "healthy")
        self.assertGreaterEqual(data["database"]["total_topics"], 1)
        self.assertIn("worker", data)
        self.assertIn("is_running", data["worker"])
        self.assertIn("recent_pipeline_runs", data)
        self.assertIn("pipeline_summary", data)

    def test_get_pipeline_metrics(self):
        # Record a test pipeline run
        ops_metrics.record_pipeline_run(
            pipeline_name="test_pipeline",
            topic_slug="test-slug",
            total_duration_ms=150.5,
            stages_ms={
                "ingestion": 25.0,
                "clustering": 55.5,
                "synthesis": 70.0,
            },
            status="success",
        )

        response = self.client.get("/api/ops/pipeline-metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertGreaterEqual(data["total_runs"], 1)
        self.assertGreaterEqual(data["success_count"], 1)
        self.assertGreater(data["avg_duration_ms"], 0)
        self.assertGreater(data["median_duration_ms"], 0)
        self.assertIn("per_stage_avg_ms", data)
        self.assertIn("slowest_recent_stages", data)
        self.assertGreaterEqual(len(data["recent_runs"]), 1)

    def test_get_source_health(self):
        # Record source executions
        ops_metrics.record_source_execution("google_news", success=True, latency_ms=12.5)
        ops_metrics.record_source_execution("reddit", success=True, latency_ms=45.0)
        ops_metrics.record_source_execution("openai", success=True, latency_ms=250.0)

        response = self.client.get("/api/ops/source-health")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn("google_news", data)
        self.assertIn("reddit", data)
        self.assertIn("x", data)
        self.assertIn("openai", data)

        gn = data["google_news"]
        self.assertIn(gn["status"], ("healthy", "degraded", "unavailable"))
        self.assertGreaterEqual(gn["success_count"], 1)
        self.assertGreater(gn["avg_latency_ms"], 0)

        # Verify no credentials or API keys leaked
        raw_text = response.text.lower()
        self.assertNotIn("sk-proj", raw_text)
        self.assertNotIn("bearer", raw_text)
        self.assertNotIn("password", raw_text)

    def test_get_worker_metrics(self):
        response = self.client.get("/api/ops/worker-metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn("scheduler_running", data)
        self.assertIn("cycle_count", data)
        self.assertIn("interval_hours", data)
        self.assertIn("last_cycle_summary", data)

    def test_ops_run_trending_control(self):
        response = self.client.post("/api/ops/run-trending?limit_per_provider=2")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("candidate_count", data)

    def test_ops_refresh_and_reprocess_topic_controls(self):
        topic = Topic(
            title="Operational Control Reprocess Test",
            slug="ops-reprocess-test",
            trending_score=0.2,
            updated_at=datetime.utcnow(),
        )
        self.db.add(topic)
        self.db.commit()

        # 1. Refresh topic
        resp_refresh = self.client.post("/api/ops/refresh-topic/ops-reprocess-test?force=true")
        self.assertEqual(resp_refresh.status_code, 200)
        data_refresh = resp_refresh.json()
        self.assertEqual(data_refresh["status"], "success")
        self.assertEqual(data_refresh["topic_slug"], "ops-reprocess-test")

        # 2. Reprocess topic
        resp_reprocess = self.client.post(
            "/api/ops/reprocess-topic/ops-reprocess-test?limit_per_source=5&min_volume_threshold=2"
        )
        self.assertEqual(resp_reprocess.status_code, 200)
        data_reprocess = resp_reprocess.json()
        self.assertEqual(data_reprocess["status"], "success")
        self.assertIn("timings", data_reprocess)
        self.assertGreater(data_reprocess["timings"]["total_duration_ms"], 0)

    def test_ops_control_security_guard_with_api_key(self):
        import os
        orig_key = os.environ.get("OPS_API_KEY")
        try:
            os.environ["OPS_API_KEY"] = "super-secret-ops-key-12345"

            # Request without header should be rejected
            resp_unauthorized = self.client.post("/api/ops/run-trending")
            self.assertEqual(resp_unauthorized.status_code, 401)
            self.assertIn("Invalid or missing X-Ops-Key", resp_unauthorized.json()["detail"])

            # Request with matching header should pass
            resp_authorized = self.client.post(
                "/api/ops/run-trending?limit_per_provider=1",
                headers={"X-Ops-Key": "super-secret-ops-key-12345"},
            )
            self.assertEqual(resp_authorized.status_code, 200)
        finally:
            if orig_key is not None:
                os.environ["OPS_API_KEY"] = orig_key
            else:
                os.environ.pop("OPS_API_KEY", None)
