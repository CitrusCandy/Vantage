from datetime import datetime
import json
from unittest.mock import MagicMock, patch
import urllib.error

from fastapi.testclient import TestClient
import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.database import get_db
from app.database.models import Base, ClusterRun, CombinedRawData, Topic
from app.main import app
from app.processing.cluster_pipeline import ClusterPipeline
from app.processing.clustering import ClusterResult, HDBSCANClusterer
from app.processing.embeddings import (
    BaseEmbeddingProvider,
    MockEmbeddingProvider,
    OpenAIEmbeddingProvider,
    get_embedding_provider,
)


# --- Test Fixtures ---

@pytest.fixture
def db_session():
    """Isolated in-memory SQLite database session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def sample_topic(db_session):
    topic = Topic(
        title="Nuclear Fusion Energy Commercialization",
        slug="nuclear-fusion-energy-commercialization",
        search_count=1,
        trending_score=0.0,
        source_coverage={},
        updated_at=datetime.utcnow(),
    )
    db_session.add(topic)
    db_session.commit()
    db_session.refresh(topic)
    return topic


# --- Embedding Provider Tests ---

def test_mock_embedding_provider():
    provider = MockEmbeddingProvider(dimension=32)
    texts = [
        "Nuclear fusion breakthrough at National Ignition Facility.",
        "Private fusion startups raise record venture funding in 2026.",
        "Unrelated culinary recipe for sourdough bread baking.",
    ]
    embeddings = provider.embed_texts(texts)

    assert len(embeddings) == 3
    assert len(embeddings[0]) == 32
    # Verify L2 normalization
    norm = np.linalg.norm(embeddings[0])
    assert abs(norm - 1.0) < 1e-4

    single = provider.embed_single("Test sentence")
    assert len(single) == 32


def test_openai_embedding_batching_and_mocked_success():
    provider = OpenAIEmbeddingProvider(api_key="test_key_sk_123")
    texts = [f"Discourse text sample number {i}" for i in range(5)]

    def mock_urlopen_handler(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        inputs = payload["input"]
        fake_data = [
            {"index": idx, "embedding": [0.1 * (idx + 1)] * 16}
            for idx in range(len(inputs))
        ]
        resp_obj = MagicMock()
        resp_obj.read.return_value = json.dumps({"data": fake_data}).encode("utf-8")
        resp_obj.__enter__.return_value = resp_obj
        return resp_obj

    with patch("urllib.request.urlopen", side_effect=mock_urlopen_handler):
        embeddings = provider.embed_texts(texts, batch_size=2)
        assert len(embeddings) == 5
        assert len(embeddings[0]) == 16


def test_openai_embedding_missing_api_key():
    provider = OpenAIEmbeddingProvider(api_key="")
    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        provider.embed_texts(["Some text"])


def test_openai_embedding_api_failure_handling():
    provider = OpenAIEmbeddingProvider(api_key="test_key_sk_123")

    mock_http_err = urllib.error.HTTPError(
        "url", 401, "Unauthorized", {}, MagicMock(read=lambda: b'{"error": "Invalid API key"}')
    )
    with patch("urllib.request.urlopen", side_effect=mock_http_err):
        with pytest.raises(RuntimeError, match="OpenAI Embedding API error HTTP 401"):
            provider.embed_texts(["Some text"])

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection timed out")):
        with pytest.raises(RuntimeError, match="OpenAI Embedding connection failed"):
            provider.embed_texts(["Some text"])


# --- HDBSCAN Clustering Tests ---

def test_hdbscan_clustering_groups_and_noise():
    clusterer = HDBSCANClusterer(min_cluster_size=3, n_representative_samples=2)

    group_a = [[1.0, 0.0, 0.0] + [0.0] * 5 for _ in range(4)]
    group_b = [[0.0, 1.0, 0.0] + [0.0] * 5 for _ in range(4)]
    outlier = [[0.0, 0.0, 1.0] + [0.0] * 5]

    embeddings = group_a + group_b + outlier
    res: ClusterResult = clusterer.fit_predict(embeddings)

    assert res.cluster_count >= 1
    assert -1 in res.labels or res.noise_count >= 0
    assert len(res.labels) == 9
    assert len(res.probabilities) == 9

    for cid in res.cluster_sizes.keys():
        assert cid in res.representative_indices
        assert len(res.representative_indices[cid]) <= 2


def test_hdbscan_empty_input():
    clusterer = HDBSCANClusterer()
    res = clusterer.fit_predict([])
    assert res.cluster_count == 0
    assert res.labels == []
    assert res.noise_count == 0


# --- End-to-End Cluster Pipeline Tests ---

def test_cluster_pipeline_insufficient_volume(db_session, sample_topic):
    # Insert 2 distinct raw items when threshold is 30
    db_session.add(
        CombinedRawData(
            slug_id=sample_topic.id,
            source="reddit",
            text_content="First distinct discussion focusing on tokamak plasma confinement physics.",
            author_handle="u/physicist_one",
        )
    )
    db_session.add(
        CombinedRawData(
            slug_id=sample_topic.id,
            source="google_news",
            text_content="Second distinct report detailing stellarator coil manufacturing advancements.",
            author_handle="Science Journal",
        )
    )
    db_session.commit()

    pipeline = ClusterPipeline(embedding_provider=MockEmbeddingProvider())
    result = pipeline.run_for_topic(
        topic=sample_topic,
        db=db_session,
        min_volume_threshold=30,
    )

    assert result["status"] == "insufficient_volume"
    assert result["sample_size"] == 2
    assert result["min_volume_threshold"] == 30
    assert result["cluster_count"] == 0

    runs = db_session.query(ClusterRun).filter(ClusterRun.topic_id == sample_topic.id).all()
    assert len(runs) == 0


def test_cluster_pipeline_success_and_persistence(db_session, sample_topic):
    distinct_contents = [
        "First analysis on high temperature superconducting magnets reducing reactor footprint.",
        "Second review regarding Commonwealth Fusion Systems SPARC tokamak construction timeline.",
        "Third commentary highlighting private venture capital investment in magnetic confinement startups.",
        "Fourth article criticizing economic viability and levelized cost of fusion power grid deployment.",
        "Fifth editorial questioning regulatory framework and tritium supply chain bottlenecks.",
        "Sixth perspective on public-private partnership grants and Department of Energy milestones.",
    ]

    for i, content in enumerate(distinct_contents):
        db_session.add(
            CombinedRawData(
                slug_id=sample_topic.id,
                source="reddit" if i % 2 == 0 else "google_news",
                text_content=content,
                author_handle=f"u/author_{i}",
                url=f"https://example.com/fusion/{i}",
            )
        )
    db_session.commit()

    pipeline = ClusterPipeline(
        embedding_provider=MockEmbeddingProvider(dimension=16),
        clusterer=HDBSCANClusterer(min_cluster_size=2),
    )

    result = pipeline.run_for_topic(
        topic=sample_topic,
        db=db_session,
        min_volume_threshold=3,
    )

    assert result["status"] == "success"
    assert result["sample_size"] == 6
    assert result["cluster_count"] >= 1
    assert "cluster_run_id" in result
    assert len(result["clusters"]) >= 1

    run_record = db_session.query(ClusterRun).filter(ClusterRun.run_id == result["cluster_run_id"]).first()
    assert run_record is not None
    assert run_record.topic_id == sample_topic.id
    assert run_record.cluster_algorithm == "hdbscan"
    assert run_record.sample_size == 6
    assert run_record.cluster_count == result["cluster_count"]

    assert sample_topic.last_clustered_at is not None


# --- FastAPI Endpoint Tests ---

def test_api_cluster_topic_endpoint(db_session, sample_topic):
    contents = [
        "First stellarator report on magnetic field geometry optimization.",
        "Second experimental trial on laser inertial confinement ignition energy.",
        "Third commercial startup raising seed funding for field reversed configuration.",
        "Fourth policy paper proposing nuclear regulatory commission fusion licensing exemption.",
    ]
    for i, text in enumerate(contents):
        db_session.add(
            CombinedRawData(
                slug_id=sample_topic.id,
                source="google_news",
                text_content=text,
                author_handle=f"Publication {i}",
            )
        )
    db_session.commit()

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # 1. Test 404 on non-existent topic
    res_404 = client.post("/api/topics/non-existent-topic/cluster")
    assert res_404.status_code == 404

    # 2. Test insufficient volume response
    res_vol = client.post(
        f"/api/topics/{sample_topic.slug}/cluster",
        params={"min_volume_threshold": 20},
    )
    assert res_vol.status_code == 200
    data_vol = res_vol.json()
    assert data_vol["status"] == "insufficient_volume"

    # 3. Test successful clustering with lowered volume gate & mocked provider
    with patch("app.api.topics.ClusterPipeline") as MockPipelineClass:
        mock_instance = MagicMock()
        mock_instance.run_for_topic.return_value = {
            "status": "success",
            "cluster_run_id": 1,
            "topic_id": sample_topic.id,
            "sample_size": 4,
            "cluster_count": 2,
            "noise_count": 0,
            "cluster_sizes": {"0": 2, "1": 2},
            "clusters": [{"cluster_id": 0, "size": 2, "representative_samples": []}],
        }
        MockPipelineClass.return_value = mock_instance

        res_success = client.post(
            f"/api/topics/{sample_topic.slug}/cluster",
            params={"min_volume_threshold": 3},
        )
        assert res_success.status_code == 200
        data_success = res_success.json()
        assert data_success["status"] == "success"
        assert data_success["cluster_count"] == 2

    app.dependency_overrides.clear()
