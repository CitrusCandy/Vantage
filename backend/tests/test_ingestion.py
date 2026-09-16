from datetime import datetime
import json
from unittest.mock import MagicMock, patch
import urllib.error

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.models import (
    Base,
    CombinedRawData,
    RawGoogleNews,
    RawReddit,
    RawX,
    Topic,
)
from app.ingestion.google_news import GoogleNewsIngestor, clean_html
from app.ingestion.merge_pipeline import MergePipeline
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.reddit import RedditIngestor
from app.ingestion.x import XScraper


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
    </channel>
</rss>
"""


def test_clean_html():
    assert clean_html("<p>Hello &amp; <b>World</b></p>") == "Hello & World"
    assert clean_html(None) == ""


def test_google_news_staging_persistence(db_session, sample_topic):
    ingestor = GoogleNewsIngestor()
    staged = ingestor.parse_and_persist(
        xml_content=SAMPLE_GOOGLE_NEWS_XML,
        topic_id=sample_topic.id,
        db=db_session,
        limit=10,
    )

    assert len(staged) == 2
    assert staged[0].title == "EU Passes Landmark AI Act"
    assert staged[0].source_name == "Reuters"
    assert staged[0].slug_id == sample_topic.id
    assert staged[1].source_name == "Bloomberg"

    # Verify rows in database staging table
    rows = db_session.query(RawGoogleNews).filter(RawGoogleNews.slug_id == sample_topic.id).all()
    assert len(rows) == 2


def test_google_news_fetch_network_failure(db_session, sample_topic):
    ingestor = GoogleNewsIngestor()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network unreachable")):
        results = ingestor.fetch_and_stage(topic=sample_topic, db=db_session)
        assert results == []


# --- Reddit Tests ---

SAMPLE_REDDIT_JSON = json.dumps({
    "data": {
        "children": [
            {
                "data": {
                    "id": "post_123",
                    "title": "Discussion on New AI Regulatory Framework",
                    "selftext": "What do you all think about the recent policies passed?",
                    "author": "tech_enthusiast",
                    "score": 342,
                    "num_comments": 89,
                    "subreddit": "technology",
                    "created_utc": 1789560000.0,
                }
            },
            {
                "data": {
                    "id": "post_456",
                    "title": "Removed spam post",
                    "selftext": "[removed]",
                    "author": "spammer",
                    "score": 0,
                    "num_comments": 0,
                    "subreddit": "news",
                    "created_utc": 1789561000.0,
                }
            },
        ]
    }
})


def test_reddit_staging_persistence(db_session, sample_topic):
    ingestor = RedditIngestor()
    staged = ingestor.parse_json_and_persist(
        raw_json=SAMPLE_REDDIT_JSON,
        topic_id=sample_topic.id,
        db=db_session,
        limit=10,
    )

    assert len(staged) == 2
    assert staged[0].post_id == "post_123"
    assert staged[0].score == 342
    assert staged[0].subreddit == "technology"
    assert "Discussion on New AI Regulatory Framework" in staged[0].body
    assert staged[0].slug_id == sample_topic.id

    # Verify rows in database staging table
    rows = db_session.query(RawReddit).filter(RawReddit.slug_id == sample_topic.id).all()
    assert len(rows) == 2


# --- X Scraper Tests ---

SAMPLE_X_HTML = """
<html>
<body>
    <article>
        <div data-testid="User-Name"><span>@tech_insider</span></div>
        <div data-testid="tweetText">Major AI regulation updates announced today across global markets. Significant compliance changes expected!</div>
        <a href="/tech_insider/status/1234567890">link</a>
        <div data-testid="like" aria-label="1500 likes">1.5K</div>
        <div data-testid="retweet" aria-label="320 retweets">320</div>
        <div data-testid="reply" aria-label="45 replies">45</div>
        <time datetime="2026-09-16T14:30:00Z"></time>
    </article>
    <article>
        <div data-testid="User-Name"><span>@policy_watcher</span></div>
        <div data-testid="tweetText">Key takeaways from the congressional hearing on frontier AI foundation models.</div>
        <a href="/policy_watcher/status/9876543210">link</a>
        <div data-testid="like">250</div>
        <div data-testid="retweet">40</div>
        <div data-testid="reply">12</div>
        <time datetime="2026-09-16T15:00:00Z"></time>
    </article>
</body>
</html>
"""


def test_x_scraper_html_parsing_and_staging(db_session, sample_topic):
    scraper = XScraper()
    staged = scraper.parse_html_and_persist(
        html_content=SAMPLE_X_HTML,
        topic_id=sample_topic.id,
        db=db_session,
        limit=10,
    )

    assert len(staged) == 2
    assert staged[0].tweet_id == "1234567890"
    assert staged[0].handle == "tech_insider"
    assert staged[0].likes == 1500
    assert staged[0].retweets == 320
    assert staged[0].slug_id == sample_topic.id

    assert staged[1].tweet_id == "9876543210"
    assert staged[1].handle == "policy_watcher"

    # Verify rows in database staging table
    rows = db_session.query(RawX).filter(RawX.slug_id == sample_topic.id).all()
    assert len(rows) == 2


def test_x_scraper_fail_soft_isolation(db_session, sample_topic):
    scraper = XScraper()
    # Scraper should safely return empty without throwing
    res = scraper.fetch_and_stage(topic=sample_topic, db=db_session)
    assert res == []


# --- Merge & Normalization Tests ---

def test_merge_pipeline_unions_all_three_sources(db_session, sample_topic):
    # Stage items in raw_google_news
    db_session.add(
        RawGoogleNews(
            slug_id=sample_topic.id,
            title="Google News AI Article",
            link="https://news.google.com/1",
            source_name="Reuters",
            snippet="Snippet text",
            published_at=datetime.utcnow(),
        )
    )
    # Stage items in raw_reddit
    db_session.add(
        RawReddit(
            slug_id=sample_topic.id,
            post_id="rd_1",
            body="Reddit post on AI ethics",
            score=100,
            num_comments=25,
            subreddit="artificial",
            author="reddit_user",
            created_utc=datetime.utcnow(),
        )
    )
    # Stage items in raw_x
    db_session.add(
        RawX(
            slug_id=sample_topic.id,
            tweet_id="tw_1",
            text="Tweet about AI governance legislation",
            likes=50,
            retweets=10,
            replies=5,
            handle="x_user",
            posted_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    merge_pipe = MergePipeline()
    result = merge_pipe.merge_topic_staging_data(topic=sample_topic, db=db_session)

    assert result["status"] == "success"
    assert result["new_records_added"] == 3
    assert result["source_breakdown"] == {"google_news": 1, "reddit": 1, "x": 1}

    # Verify rows in combined_raw_data
    combined_rows = db_session.query(CombinedRawData).filter(CombinedRawData.slug_id == sample_topic.id).all()
    assert len(combined_rows) == 3
    sources = {r.source for r in combined_rows}
    assert sources == {"google_news", "reddit", "x"}

    # Verify source attribution & URLs
    gn_c = [r for r in combined_rows if r.source == "google_news"][0]
    assert gn_c.url == "https://news.google.com/1"
    assert gn_c.author_handle == "Reuters"

    rd_c = [r for r in combined_rows if r.source == "reddit"][0]
    assert rd_c.author_handle == "u/reddit_user"
    assert rd_c.engagement_metrics["score"] == 100

    x_c = [r for r in combined_rows if r.source == "x"][0]
    assert x_c.author_handle == "@x_user"
    assert x_c.engagement_metrics["likes"] == 50


def test_merge_pipeline_idempotency_repeated_runs(db_session, sample_topic):
    # Add staging records
    db_session.add(
        RawGoogleNews(
            slug_id=sample_topic.id,
            title="Single News Story",
            link="https://news.google.com/unique1",
            source_name="AP",
            published_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    merge_pipe = MergePipeline()
    # First merge
    res1 = merge_pipe.merge_topic_staging_data(topic=sample_topic, db=db_session)
    assert res1["new_records_added"] == 1

    # Second merge (should not duplicate records)
    res2 = merge_pipe.merge_topic_staging_data(topic=sample_topic, db=db_session)
    assert res2["new_records_added"] == 0

    combined_rows = db_session.query(CombinedRawData).filter(CombinedRawData.slug_id == sample_topic.id).all()
    assert len(combined_rows) == 1
