from datetime import datetime
import json
from unittest.mock import MagicMock, patch
import urllib.error

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.models import Base, RawData, Topic
from app.ingestion.base import BaseIngestor
from app.ingestion.google_news import GoogleNewsIngestor, clean_html
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.reddit import RedditIngestor
from app.ingestion.schemas import IngestedItem


# --- Test Fixtures ---

@pytest.fixture
def db_session():
    """Create an isolated in-memory SQLite database session for testing."""
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
    """Create a sample Topic in the database."""
    topic = Topic(
        title="Artificial Intelligence Regulation",
        slug="artificial-intelligence-regulation",
        search_count=0,
        trending_score=0.0,
        source_coverage={},
        updated_at=datetime.utcnow(),
    )
    db_session.add(topic)
    db_session.commit()
    db_session.refresh(topic)
    return topic


# --- Google News Tests ---

SAMPLE_GOOGLE_NEWS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>Google News - AI Regulation</title>
        <item>
            <title>EU Passes Landmark AI Act</title>
            <link>https://news.google.com/articles/CAIiEA123</link>
            <description>&lt;p&gt;The European Parliament has approved new comprehensive rules on AI.&lt;/p&gt;</description>
            <source url="https://reuters.com">Reuters</source>
            <pubDate>Wed, 16 Sep 2026 12:00:00 GMT</pubDate>
        </item>
        <item>
            <title>Tech Giants Respond to AI Legislation</title>
            <link>https://news.google.com/articles/CAIiEA456</link>
            <description>Industry leaders comment on the new regulatory framework.</description>
            <source url="https://bloomberg.com">Bloomberg</source>
            <pubDate>Wed, 16 Sep 2026 13:00:00 GMT</pubDate>
        </item>
        <item>
            <!-- Malformed item missing title -->
            <link>https://news.google.com/articles/empty</link>
            <description></description>
        </item>
    </channel>
</rss>
"""


def test_clean_html():
    assert clean_html("<p>Hello &amp; <b>World</b></p>") == "Hello & World"
    assert clean_html(None) == ""


def test_google_news_rss_parsing():
    ingestor = GoogleNewsIngestor()
    items = ingestor.parse_rss_content(SAMPLE_GOOGLE_NEWS_XML, limit=10)

    assert len(items) == 2
    assert items[0].source == "google_news"
    assert "EU Passes Landmark AI Act" in items[0].text_content
    assert items[0].url == "https://news.google.com/articles/CAIiEA123"
    assert items[0].author_handle == "Reuters"
    assert items[0].engagement_metrics == {"publisher": "Reuters"}

    assert items[1].author_handle == "Bloomberg"


def test_google_news_fetch_network_failure():
    ingestor = GoogleNewsIngestor()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network unreachable")):
        results = ingestor.fetch_items("AI Regulation")
        assert results == []


def test_google_news_malformed_xml():
    ingestor = GoogleNewsIngestor()
    results = ingestor.parse_rss_content(b"<invalid>xml", limit=10)
    assert results == []


# --- Reddit Tests ---

SAMPLE_REDDIT_RESPONSE = {
    "data": {
        "children": [
            {
                "data": {
                    "title": "Discussion on New AI Regulatory Framework",
                    "selftext": "What do you all think about the recent policies passed?",
                    "author": "tech_enthusiast",
                    "permalink": "/r/technology/comments/123/ai_discussion/",
                    "score": 342,
                    "num_comments": 89,
                    "upvote_ratio": 0.94,
                    "subreddit": "technology",
                    "created_utc": 1789560000.0,
                }
            },
            {
                "data": {
                    "title": "Removed spam post",
                    "selftext": "[removed]",
                    "author": "spammer",
                    "permalink": "/r/news/comments/456/spam/",
                    "score": 0,
                    "num_comments": 0,
                    "upvote_ratio": 0.5,
                    "subreddit": "news",
                    "created_utc": 1789561000.0,
                }
            },
            {
                "data": {
                    # Empty text item
                    "title": "",
                    "selftext": "",
                }
            },
        ]
    }
}


def test_reddit_response_normalization():
    ingestor = RedditIngestor()
    items = ingestor.parse_json_response(json.dumps(SAMPLE_REDDIT_RESPONSE), limit=10)

    assert len(items) == 2
    first = items[0]
    assert first.source == "reddit"
    assert "Discussion on New AI Regulatory Framework" in first.text_content
    assert "What do you all think" in first.text_content
    assert first.author_handle == "u/tech_enthusiast"
    assert first.url == "https://www.reddit.com/r/technology/comments/123/ai_discussion/"
    assert first.engagement_metrics["score"] == 342
    assert first.engagement_metrics["num_comments"] == 89
    assert first.engagement_metrics["subreddit"] == "technology"

    # [removed] selftext is cleared, but title is retained
    second = items[1]
    assert second.text_content == "Removed spam post"


def test_reddit_rate_limit_and_error_handling():
    ingestor = RedditIngestor()

    # Test HTTP 429 Rate Limit error handling
    mock_http_error = urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)
    with patch("urllib.request.urlopen", side_effect=mock_http_error):
        results = ingestor.fetch_items("AI Regulation")
        assert results == []

    # Test JSON decode error
    results = ingestor.parse_json_response("invalid json string")
    assert results == []


# --- Combined Pipeline Tests ---

class MockIngestor(BaseIngestor):
    def __init__(self, source_name: str, items: list, should_fail: bool = False):
        self.source_name = source_name
        self._items = items
        self._should_fail = should_fail

    def fetch_items(self, query: str, limit: int = 25, timeout_seconds: float = 10.0):
        if self._should_fail:
            raise RuntimeError(f"Connection failed for {self.source_name}")
        return self._items


def test_combined_pipeline_success(db_session, sample_topic):
    mock_gn_item = IngestedItem(
        source="google_news",
        text_content="Google News Article Text",
        url="https://news.google.com/1",
        author_handle="Reuters",
        engagement_metrics={},
        created_at=datetime.utcnow(),
    )
    mock_reddit_item = IngestedItem(
        source="reddit",
        text_content="Reddit Discussion Content",
        url="https://reddit.com/r/1",
        author_handle="u/sample",
        engagement_metrics={"score": 100},
        created_at=datetime.utcnow(),
    )

    pipeline = IngestionPipeline(
        ingestors=[
            MockIngestor("google_news", [mock_gn_item]),
            MockIngestor("reddit", [mock_reddit_item]),
        ]
    )

    result = pipeline.run(topic=sample_topic, db=db_session)

    assert result["total_persisted"] == 2
    assert result["source_breakdown"] == {"google_news": 1, "reddit": 1}
    assert sample_topic.search_count == 1
    assert sample_topic.source_coverage["google_news"] == 1
    assert sample_topic.source_coverage["reddit"] == 1
    assert sample_topic.source_coverage["total_raw_items"] == 2

    # Verify rows in database
    db_items = db_session.query(RawData).filter(RawData.topic_id == sample_topic.id).all()
    assert len(db_items) == 2
    sources = {item.source for item in db_items}
    assert sources == {"google_news", "reddit"}


def test_combined_pipeline_isolated_failure(db_session, sample_topic):
    """Ensure one failing source does not stop other sources from persisting."""
    mock_reddit_item = IngestedItem(
        source="reddit",
        text_content="Reddit Discussion Content",
        url="https://reddit.com/r/1",
        author_handle="u/sample",
        engagement_metrics={"score": 50},
        created_at=datetime.utcnow(),
    )

    pipeline = IngestionPipeline(
        ingestors=[
            MockIngestor("google_news", [], should_fail=True),
            MockIngestor("reddit", [mock_reddit_item]),
        ]
    )

    result = pipeline.run(topic=sample_topic, db=db_session)

    assert result["total_persisted"] == 1
    assert result["source_breakdown"]["reddit"] == 1
    assert result["source_breakdown"]["google_news"] == 0
    assert "google_news" in result["source_errors"]

    # Verify database persistence for the successful source
    db_items = db_session.query(RawData).filter(RawData.topic_id == sample_topic.id).all()
    assert len(db_items) == 1
    assert db_items[0].source == "reddit"
