import time
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.telemetry import PipelineTimingTracker, StageTiming
from app.database.database import Base, get_db
from app.main import app


class TestTelemetryAndTiming(unittest.TestCase):
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

    def test_stage_timing_and_pipeline_tracker(self):
        tracker = PipelineTimingTracker("test_pipeline")

        with tracker.track("stage_a", items_count=100):
            time.sleep(0.01)

        with tracker.track("stage_b", status="ok"):
            time.sleep(0.005)

        tracker.record_manual("stage_manual", 15.5, note="external")

        summary = tracker.get_summary()
        self.assertEqual(summary["pipeline_name"], "test_pipeline")
        self.assertIn("stage_a", summary["stages_ms"])
        self.assertIn("stage_b", summary["stages_ms"])
        self.assertIn("stage_manual", summary["stages_ms"])
        self.assertGreaterEqual(summary["stages_ms"]["stage_a"], 5.0)
        self.assertGreaterEqual(summary["total_duration_ms"], 15.0)

    def test_http_process_time_header_and_middleware(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("X-Process-Time", resp.headers)
        self.assertTrue(resp.headers["X-Process-Time"].endswith("ms"))


if __name__ == "__main__":
    unittest.main()
