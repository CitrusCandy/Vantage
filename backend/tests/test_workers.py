from datetime import datetime, timedelta
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.database import Base, get_db
from app.database.models import CombinedRawData, Topic
from app.main import app
from app.workers.discovery import (
    BaseTrendProvider,
    CandidateTopic,
    MockTrendProvider,
    TrendDiscoveryService,
    normalize_topic_title,
)
from app.workers.scheduler import BackgroundScheduler
from app.workers.topic_refresh import RefreshSummary, TopicRefreshWorker
from app.workers.trending import TrendingBreakdown, TrendingScorer
from app.workers.worker_config import WorkerConfig


class FailingTrendProvider(BaseTrendProvider):
    source_name = "failing_source"

    def discover_candidates(self, limit: int = 10, timeout_seconds: float = 10.0):
        raise ConnectionError("Network timeout connecting to external trends service")


class TestWorkerConfigAndScorer(unittest.TestCase):
    def test_worker_config_defaults(self):
        config = WorkerConfig()
        self.assertAlmostEqual(config.w1_mention_velocity, 0.35)
        self.assertAlmostEqual(config.w2_unique_sources, 0.25)
        self.assertAlmostEqual(config.w3_engagement_rate, 0.25)
        self.assertAlmostEqual(config.w4_time_decay, 0.15)
        self.assertEqual(config.worker_interval_hours, 2.0)
        self.assertEqual(config.min_trending_score_for_refresh, 0.20)

    def test_trending_scorer_formula_calculation(self):
        config = WorkerConfig(
            w1_mention_velocity=0.40,
            w2_unique_sources=0.30,
            w3_engagement_rate=0.20,
            w4_time_decay=0.10,
            decay_half_life_hours=24.0,
        )
        scorer = TrendingScorer(config=config)

        # 3 unique sources (norm = 1.0), 1.0 velocity, 1.0 engagement, 0 decay (0 hours since update)
        breakdown = scorer.compute_score(
            mention_velocity=1.0,
            unique_source_count=3,
            engagement_rate=1.0,
            hours_since_update=0.0,
        )
        # Expected: 0.40*1.0 + 0.30*1.0 + 0.20*1.0 - 0.10*0.0 = 0.90
        self.assertAlmostEqual(breakdown.raw_score, 0.90, places=3)
        self.assertAlmostEqual(breakdown.final_score, 0.90, places=3)
        self.assertEqual(breakdown.unique_source_count, 3)

    def test_trending_scorer_bounds_clamping(self):
        scorer = TrendingScorer()
        # High out-of-bound inputs
        breakdown = scorer.compute_score(
            mention_velocity=5.0,
            unique_source_count=10,
            engagement_rate=10.0,
            hours_since_update=0.0,
        )
        self.assertLessEqual(breakdown.final_score, 1.0)
        self.assertGreaterEqual(breakdown.final_score, 0.0)

        # Extreme decay inputs
        breakdown_decay = scorer.compute_score(
            mention_velocity=0.0,
            unique_source_count=0,
            engagement_rate=0.0,
            hours_since_update=1000.0,
        )
        self.assertEqual(breakdown_decay.final_score, 0.0)


class TestTrendDiscovery(unittest.TestCase):
    def test_topic_title_normalization(self):
        title1 = "Autonomous AI Agents in Healthcare!"
        title2 = "autonomous  ai  agents in healthcare"
        self.assertEqual(normalize_topic_title(title1), normalize_topic_title(title2))

    def test_trend_discovery_deduplication_and_isolation(self):
        provider1 = MockTrendProvider(
            source_name="p1",
            candidate_list=["Artificial Intelligence in Medicine", "Quantum Computing Advancements"],
        )
        provider2 = MockTrendProvider(
            source_name="p2",
            candidate_list=["artificial intelligence in medicine", "Solid State Batteries"],
        )
        provider_fail = FailingTrendProvider()

        service = TrendDiscoveryService(providers=[provider1, provider2, provider_fail])
        results = service.discover_trending_topics(limit_per_provider=5)

        # Even with provider_fail crashing, p1 and p2 must succeed
        self.assertGreaterEqual(len(results), 2)

        # "Artificial Intelligence in Medicine" appeared in p1 & p2 -> deduplicated into 1 candidate with both sources
        ai_med = next((c for c in results if "artificial intelligence in medicine" in c.normalized_key), None)
        self.assertIsNotNone(ai_med)
        self.assertIn("p1", ai_med.sources)
        self.assertIn("p2", ai_med.sources)
        self.assertGreater(ai_med.confidence, 0.5)


class TestTopicRefreshWorker(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_trending_score_db_calculation(self):
        # Create topic
        topic = Topic(
            title="Global AI Governance",
            slug="global-ai-governance",
            search_count=10,
            trending_score=0.0,
            updated_at=datetime.utcnow(),
        )
        self.db.add(topic)
        self.db.commit()
        self.db.refresh(topic)

        # Add CombinedRawData records from multiple sources with engagement
        now = datetime.utcnow()
        for i in range(15):
            r = CombinedRawData(
                slug_id=topic.id,
                source="google_news" if i % 3 == 0 else ("reddit" if i % 3 == 1 else "x"),
                text_content=f"Discourse sample {i} on AI regulation and governance",
                url=f"https://example.com/post/{i}",
                engagement_metrics={"score": 100, "likes": 50, "retweets": 20, "num_comments": 10},
                is_flagged_bot=False,
                created_at=now - timedelta(minutes=i * 10),
            )
            self.db.add(r)
        self.db.commit()

        scorer = TrendingScorer()
        breakdown = scorer.calculate_topic_score(topic=topic, db=self.db, reference_time=now)
        self.assertGreater(breakdown.final_score, 0.1)
        self.assertEqual(breakdown.unique_source_count, 3)

    def test_topic_refresh_cycle_stagnant_skipping(self):
        # Stagnant topic (idle for 100 hours with 0 score)
        stagnant_topic = Topic(
            title="Old Cold Topic",
            slug="old-cold-topic",
            search_count=1,
            trending_score=0.05,
            last_clustered_at=datetime.utcnow() - timedelta(hours=100),
            updated_at=datetime.utcnow() - timedelta(hours=100),
        )
        # Active trending topic
        active_topic = Topic(
            title="Hot Breaking Topic",
            slug="hot-breaking-topic",
            search_count=50,
            trending_score=0.85,
            last_clustered_at=None,
            updated_at=datetime.utcnow(),
        )
        self.db.add_all([stagnant_topic, active_topic])
        self.db.commit()

        mock_ingestion = MagicMock()
        mock_clustering = MagicMock()
        mock_clustering.run_for_topic.return_value = {"status": "insufficient_volume", "count": 5}
        mock_perspective = MagicMock()

        worker = TopicRefreshWorker(
            ingestion_pipeline=mock_ingestion,
            cluster_pipeline=mock_clustering,
            perspective_pipeline=mock_perspective,
        )

        summary = worker.run_refresh_cycle(db=self.db, force_refresh_all=False)

        self.assertIn("old-cold-topic", summary.skipped_stagnant_topics)
        self.assertIn("hot-breaking-topic", summary.refreshed_topics)
        # Ingestion pipeline should only have been called for the active topic
        mock_ingestion.run.assert_called_once()


class TestBackgroundSchedulerAndApi(unittest.TestCase):
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

    def test_scheduler_lifecycle(self):
        sched = BackgroundScheduler(config=WorkerConfig(worker_interval_hours=10.0))
        self.assertFalse(sched.is_running)

        sched.start()
        self.assertTrue(sched.is_running)
        status = sched.get_status()
        self.assertTrue(status["is_running"])

        sched.stop()
        self.assertFalse(sched.is_running)

    def test_get_trending_topics_endpoint(self):
        # Insert topics with different scores
        t1 = Topic(title="Topic Low", slug="topic-low", search_count=1, trending_score=0.1, updated_at=datetime.utcnow())
        t2 = Topic(title="Topic High", slug="topic-high", search_count=5, trending_score=0.9, updated_at=datetime.utcnow())
        t3 = Topic(title="Topic Mid", slug="topic-mid", search_count=2, trending_score=0.5, updated_at=datetime.utcnow())
        self.db.add_all([t1, t2, t3])
        self.db.commit()

        # Query trending
        response = self.client.get("/api/topics/trending?limit=10&min_score=0.2")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["slug"], "topic-high")
        self.assertEqual(data[1]["slug"], "topic-mid")

    def test_worker_api_endpoints(self):
        # Test worker status
        resp_status = self.client.get("/api/workers/status")
        self.assertEqual(resp_status.status_code, 200)

        # Test candidate discovery endpoint
        with patch.object(
            TrendDiscoveryService,
            "discover_trending_topics",
            return_value=[
                CandidateTopic(
                    title="Test Candidate Trend",
                    normalized_key="test candidate trend",
                    sources={"google_trends", "reddit_trends"},
                    confidence=0.75,
                )
            ],
        ):
            resp_disc = self.client.post("/api/workers/discover-trends?limit_per_provider=5")
            self.assertEqual(resp_disc.status_code, 200)
            data = resp_disc.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["candidate_count"], 1)
            self.assertEqual(data["candidates"][0]["title"], "Test Candidate Trend")


if __name__ == "__main__":
    unittest.main()
