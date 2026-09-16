from datetime import datetime
import json
from unittest.mock import MagicMock, patch
import urllib.error

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.database import get_db
from app.database.models import (
    Base,
    ClusterRun,
    CombinedRawData,
    Perspective,
    Topic,
)
from app.llm.perspective import (
    BasePerspectiveSynthesizer,
    MockPerspectiveSynthesizer,
    OpenAIPerspectiveSynthesizer,
    get_perspective_synthesizer,
)
from app.llm.pipeline import PerspectivePipeline
from app.llm.schemas import (
    PerspectiveItem,
    PerspectiveSynthesisOutput,
    SampleQuote,
)
from app.main import app
from app.processing.cluster_pipeline import ClusterPipeline
from app.processing.embeddings import MockEmbeddingProvider


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
        title="Autonomous AI Agents in Healthcare",
        slug="autonomous-ai-agents-in-healthcare",
        search_count=1,
        trending_score=0.0,
        source_coverage={},
        updated_at=datetime.utcnow(),
    )
    db_session.add(topic)
    db_session.commit()
    db_session.refresh(topic)
    return topic


# --- Schema Validation Tests ---

def test_perspective_schemas():
    quote = SampleQuote(
        text="AI agents significantly reduce diagnostic turnaround times in clinical radiology.",
        source="google_news",
        url="https://news.example.com/radiology",
    )
    assert quote.source == "google_news"

    item = PerspectiveItem(
        type="Clinical Efficiency Advocates",
        estimated_share=0.55,
        summary="Clinicians and hospital administrators highlight dramatic reductions in diagnostic delays.",
        key_arguments=[
            "Reduces physician burnout and administrative burden.",
            "Improves patient triage times in emergency care.",
        ],
        sample_quotes=[quote],
    )
    assert item.estimated_share == 0.55
    assert len(item.key_arguments) == 2

    output = PerspectiveSynthesisOutput(
        core_topic="Autonomous AI Agents in Healthcare",
        perspectives=[item],
        confidence_note="High confidence derived from 45 medical news articles and practitioner discussions.",
    )
    assert output.core_topic == "Autonomous AI Agents in Healthcare"
    assert len(output.perspectives) == 1


# --- Synthesizer Provider Tests ---

def test_mock_perspective_synthesizer():
    synthesizer = MockPerspectiveSynthesizer()
    clusters = [
        {
            "cluster_id": 0,
            "size": 15,
            "share": 0.6,
            "representative_samples": [
                {
                    "source": "google_news",
                    "author_handle": "NEJM",
                    "url": "https://nejm.org/article1",
                    "text_content": "Clinical trial demonstrates 94% accuracy in early diagnostic oncology screening.",
                }
            ],
        },
        {
            "cluster_id": 1,
            "size": 10,
            "share": 0.4,
            "representative_samples": [
                {
                    "source": "reddit",
                    "author_handle": "u/bioethics_dr",
                    "url": "https://reddit.com/r/medicine/1",
                    "text_content": "Ethical considerations regarding liability, hallucination risk, and patient consent.",
                }
            ],
        },
    ]

    result: PerspectiveSynthesisOutput = synthesizer.synthesize(
        topic_title="Autonomous AI Agents in Healthcare",
        cluster_payloads=clusters,
        total_sample_size=25,
    )

    assert result.core_topic == "Autonomous AI Agents in Healthcare"
    assert len(result.perspectives) == 2
    assert len(result.perspectives[0].sample_quotes) >= 1
    assert "google_news" in result.perspectives[0].sample_quotes[0].source


def test_openai_perspective_synthesizer_mocked_success():
    synthesizer = OpenAIPerspectiveSynthesizer(api_key="sk-test-key", model_name="gpt-4o-mini")

    fake_output = {
        "core_topic": "Autonomous AI Agents in Healthcare",
        "perspectives": [
            {
                "type": "Clinical Workflow Optimization",
                "estimated_share": 0.65,
                "summary": "AI agents streamline diagnostic triage and documentation.",
                "key_arguments": ["Reduces turnaround time", "Standardizes charting"],
                "sample_quotes": [
                    {
                        "text": "Clinicians save 2 hours daily on documentation.",
                        "source": "google_news",
                        "url": "https://news.example.com/1",
                    }
                ],
            }
        ],
        "confidence_note": "Robust multi-source discourse analysis across 50 records.",
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "choices": [{"message": {"content": json.dumps(fake_output)}}]
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res: PerspectiveSynthesisOutput = synthesizer.synthesize(
            topic_title="Autonomous AI Agents in Healthcare",
            cluster_payloads=[],
            total_sample_size=10,
        )
        assert res.core_topic == "Autonomous AI Agents in Healthcare"
        assert len(res.perspectives) == 1
        assert res.perspectives[0].type == "Clinical Workflow Optimization"


def test_openai_synthesizer_error_handling():
    synthesizer = OpenAIPerspectiveSynthesizer(api_key="sk-test-key")

    # 1. HTTP 500 error
    mock_http_err = urllib.error.HTTPError(
        "url", 500, "Internal Server Error", {}, MagicMock(read=lambda: b"Server overloaded")
    )
    with patch("urllib.request.urlopen", side_effect=mock_http_err):
        with pytest.raises(RuntimeError, match="OpenAI Perspective API error HTTP 500"):
            synthesizer.synthesize("Topic", [], 10)

    # 2. Network connection error
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("DNS resolution failed")):
        with pytest.raises(RuntimeError, match="OpenAI connection error"):
            synthesizer.synthesize("Topic", [], 10)


# --- End-to-End Perspective Pipeline Tests ---

def test_perspective_pipeline_and_database_persistence(db_session, sample_topic):
    # Insert 6 distinct items into combined_raw_data
    contents = [
        "First article on AI clinical trial results in automated diagnostic triage.",
        "Second review regarding regulatory compliance and FDA software clearance.",
        "Third commentary highlighting hospital executive budget allocations for automation.",
        "Fourth article criticizing liability assignment when AI diagnostic errors occur.",
        "Fifth editorial questioning malpractice insurance coverage for autonomous agents.",
        "Sixth perspective on medical staff training and patient communication protocols.",
    ]

    for i, content in enumerate(contents):
        db_session.add(
            CombinedRawData(
                slug_id=sample_topic.id,
                source="reddit" if i % 2 == 0 else "google_news",
                text_content=content,
                author_handle=f"u/clinician_{i}",
                url=f"https://example.com/healthcare/{i}",
            )
        )
    db_session.commit()

    pipeline = PerspectivePipeline(
        synthesizer=MockPerspectiveSynthesizer(),
        cluster_pipeline=ClusterPipeline(embedding_provider=MockEmbeddingProvider(dimension=16)),
    )

    result = pipeline.run_synthesis_for_topic(
        topic=sample_topic,
        db=db_session,
        min_volume_threshold=3,
    )

    assert result["status"] == "success"
    assert result["perspectives_count"] >= 1
    assert result["core_topic"] == sample_topic.title

    # Verify rows persisted in Perspective database model
    perspectives_db = db_session.query(Perspective).filter(Perspective.topic_id == sample_topic.id).all()
    assert len(perspectives_db) >= 1
    assert perspectives_db[0].topic_id == sample_topic.id
    assert perspectives_db[0].perspective_type is not None
    assert "summary" in perspectives_db[0].summary_points
    assert isinstance(perspectives_db[0].sample_quotes, list)


def test_perspective_pipeline_insufficient_volume(db_session, sample_topic):
    # Only 1 item when threshold is 30
    db_session.add(
        CombinedRawData(
            slug_id=sample_topic.id,
            source="google_news",
            text_content="Single news piece on medical imaging algorithms.",
            author_handle="MedTech Daily",
        )
    )
    db_session.commit()

    pipeline = PerspectivePipeline(synthesizer=MockPerspectiveSynthesizer())
    result = pipeline.run_synthesis_for_topic(
        topic=sample_topic,
        db=db_session,
        min_volume_threshold=30,
    )

    assert result["status"] == "insufficient_volume"
    assert result["perspectives"] == []

    # Ensure no perspectives were persisted
    perspectives_db = db_session.query(Perspective).filter(Perspective.topic_id == sample_topic.id).all()
    assert len(perspectives_db) == 0


# --- API Endpoint Tests ---

def test_api_synthesize_endpoint(db_session, sample_topic):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # 1. 404 on non-existent topic
    res_404 = client.post("/api/topics/non-existent-topic/synthesize")
    assert res_404.status_code == 404

    # 2. Successful synthesis with mocked pipeline
    with patch("app.api.topics.PerspectivePipeline") as MockPipeClass:
        mock_instance = MagicMock()
        mock_instance.run_synthesis_for_topic.return_value = {
            "status": "success",
            "topic_id": sample_topic.id,
            "topic_slug": sample_topic.slug,
            "core_topic": sample_topic.title,
            "perspectives_count": 2,
            "perspectives": [
                {
                    "per_id": 1,
                    "type": "Efficiency Proponents",
                    "estimated_share": 0.6,
                    "summary_points": {"summary": "Workflow speedup", "key_arguments": ["Saves time"]},
                    "sample_quotes": [],
                }
            ],
            "confidence_note": "Mocked synthesis output.",
        }
        MockPipeClass.return_value = mock_instance

        res = client.post(f"/api/topics/{sample_topic.slug}/synthesize")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["perspectives_count"] == 2

    app.dependency_overrides.clear()
