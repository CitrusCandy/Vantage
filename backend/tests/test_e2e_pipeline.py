from datetime import datetime, timedelta
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.database import Base, get_db
from app.database.models import (
    ClusterRun,
    CombinedRawData,
    Perspective,
    RawGoogleNews,
    RawReddit,
    RawX,
    Topic,
)
from app.ingestion.merge_pipeline import MergePipeline
from app.ingestion.pipeline import IngestionPipeline
from app.llm.perspective import MockPerspectiveSynthesizer
from app.llm.pipeline import PerspectivePipeline
from app.main import app
from app.processing.cluster_pipeline import ClusterPipeline
from app.processing.embeddings import MockEmbeddingProvider
from app.workers.trending import TrendingScorer


class TestEndToEndPipeline(unittest.TestCase):
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

    def test_full_pipeline_flow_step_by_step(self):
        """Verify step-by-step: Creation -> Ingestion -> Staging -> Merge -> Clustering -> Synthesis -> Topic Retrieval."""
        # 1. Create Topic
        create_resp = self.client.post("/api/topics", json={"title": "Next-Gen Quantum Computing"})
        self.assertEqual(create_resp.status_code, 201)
        topic_data = create_resp.json()
        slug = topic_data["slug"]
        topic_id = topic_data["id"]

        topic = self.db.query(Topic).filter(Topic.id == topic_id).first()
        self.assertIsNotNone(topic)

        # 2. Stage multi-source discourse items with diverse perspectives
        now = datetime.utcnow()
        topics_samples = [
            ("Superconducting Qubits", "Benchmarking superconducting transmon coherence times and fidelity rates."),
            ("Trapped Ion Architectures", "High connectivity and ultra-low gate error rates demonstrated in trapped ion systems."),
            ("Topological Quantum Computing", "Majorana zero modes and hardware-level fault-tolerance breakthroughs."),
            ("Post-Quantum Cryptography", "NIST standardization of lattice-based encryption algorithms to resist Shor's algorithm."),
            ("Dilution Refrigerator Infrastructure", "Cryogenic cooling capacity scaling challenges in multi-rack data centers."),
            ("Quantum Chemistry Simulations", "Simulating complex catalyst reaction dynamics for nitrogen fixation on quantum hardware."),
            ("Quantum Key Distribution", "Satellite-to-ground entangled photon links for secure communications infrastructure."),
            ("Photonic Quantum Processors", "Room-temperature optical quantum computing scaling with silicon photonics."),
            ("Quantum Machine Learning", "Evaluating barren plateaus and expressive capacity in parameterized quantum circuits."),
            ("Neutral Atom Arrays", "Optical tweezers manipulating 1,000+ Rydberg neutral atom arrays in 2D grids."),
            ("Silicon Spin Qubits", "Compatibility with existing CMOS foundry fabrication lines for scalable spin qubits."),
            ("Quantum Error Correction", "Demonstrating physical-to-logical qubit threshold improvements using surface codes."),
        ]

        for i, (title_sub, desc) in enumerate(topics_samples):
            g = RawGoogleNews(
                slug_id=topic.id,
                title=f"{title_sub} breakthrough in commercial benchmarks {i}",
                snippet=desc,
                link=f"https://news.google.com/articles/{i}",
                source_name=f"TechJournal_{i}",
                published_at=now - timedelta(minutes=i * 5),
            )
            r = RawReddit(
                slug_id=topic.id,
                post_id=f"t3_post_{i}",
                body=f"Detailed technical deep dive into {title_sub}: {desc} What are the scaling implications?",
                score=300 + i * 15,
                num_comments=45 + i,
                subreddit="technology",
                author=f"researcher_{i}",
                created_utc=now - timedelta(minutes=i * 5),
            )
            x = RawX(
                slug_id=topic.id,
                tweet_id=f"18900{i}",
                text=f"New data on {title_sub}: {desc} Fast timeline to enterprise production. #{title_sub.replace(' ', '')}",
                likes=500 + i * 20,
                retweets=80 + i * 5,
                replies=12,
                handle=f"lead_{i}",
                posted_at=now - timedelta(minutes=i * 5),
            )
            self.db.add_all([g, r, x])
        self.db.commit()

        # 3. Merge staging tables into CombinedRawData
        merge_pipeline = MergePipeline()
        merge_stats = merge_pipeline.merge_topic_staging_data(topic=topic, db=self.db)
        self.assertEqual(merge_stats["total_combined_count"], 36)
        self.assertEqual(self.db.query(CombinedRawData).filter(CombinedRawData.slug_id == topic.id).count(), 36)

        # 4. HDBSCAN Vector Clustering with Mock Embeddings
        cluster_pipeline = ClusterPipeline(embedding_provider=MockEmbeddingProvider(dimension=64))
        cluster_res = cluster_pipeline.run_for_topic(topic=topic, db=self.db, min_volume_threshold=10)
        self.assertEqual(cluster_res["status"], "success")
        self.assertGreaterEqual(cluster_res["cluster_count"], 1)

        # 5. LLM Perspective Synthesis with Mock Synthesizer
        perspective_pipeline = PerspectivePipeline(synthesizer=MockPerspectiveSynthesizer())
        synthesis_res = perspective_pipeline.run_synthesis_for_topic(
            topic=topic,
            db=self.db,
            min_volume_threshold=10,
            cluster_data=cluster_res,
        )
        self.assertEqual(synthesis_res["status"], "success")
        self.assertGreaterEqual(synthesis_res["perspectives_count"], 1)

        # 6. Check Perspectives persisted in database
        perspectives = self.db.query(Perspective).filter(Perspective.topic_id == topic.id).all()
        self.assertGreaterEqual(len(perspectives), 1)
        self.assertTrue(hasattr(perspectives[0], "perspective_type"))
        self.assertTrue(hasattr(perspectives[0], "sample_quotes"))

        # 7. Query Topic via API endpoint
        detail_resp = self.client.get(f"/api/topics/{slug}")
        self.assertEqual(detail_resp.status_code, 200)
        detail_data = detail_resp.json()
        self.assertEqual(detail_data["slug"], slug)
        self.assertGreaterEqual(len(detail_data["perspectives"]), 1)
        self.assertEqual(detail_data["source_coverage"]["total_combined"], 36)

    def test_run_full_pipeline_endpoint(self):
        """Test the unified /api/topics/{slug}/run-pipeline endpoint."""
        # Create Topic
        create_resp = self.client.post("/api/topics", json={"title": "Autonomous Vehicle Safety"})
        self.assertEqual(create_resp.status_code, 201)
        slug = create_resp.json()["slug"]

        # Mock Ingestion and Synthesis to avoid external API calls
        mock_ingestion_result = {
            "status": "success",
            "counts": {"google_news": 15, "reddit": 15, "x": 15},
            "total_staged": 45,
            "merge": {"total_combined": 45},
        }

        with patch.object(IngestionPipeline, "run", return_value=mock_ingestion_result), \
             patch.object(ClusterPipeline, "run_for_topic", return_value={"status": "success", "clusters_count": 2}), \
             patch.object(PerspectivePipeline, "run_synthesis_for_topic", return_value={"status": "success", "perspectives_count": 2}):

            # Run full pipeline endpoint
            response = self.client.post(f"/api/topics/{slug}/run-pipeline?limit_per_source=15&min_volume_threshold=5")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "success")
            self.assertIn("ingestion", data)
            self.assertIn("clustering", data)


if __name__ == "__main__":
    unittest.main()
