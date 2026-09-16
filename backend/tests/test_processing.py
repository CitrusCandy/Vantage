from datetime import datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.models import Base, CombinedRawData, Topic
from app.processing.bot_detector import BotDetector
from app.processing.minhash_lsh import MinHash, MinHashLSH
from app.processing.processor import DiscourseProcessor
from app.processing.text_cleaner import (
    clean_text_for_display,
    extract_shingles,
    normalize_text_for_matching,
)


# --- Fixtures ---

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
        title="Global Semiconductor Supply Chain",
        slug="global-semiconductor-supply-chain",
        search_count=1,
        trending_score=0.0,
        source_coverage={},
        updated_at=datetime.utcnow(),
    )
    db_session.add(topic)
    db_session.commit()
    db_session.refresh(topic)
    return topic


# --- Text Cleaning & Shingling Tests ---

def test_text_cleaner_and_normalizer():
    raw_text = "Breaking: <p>TSMC expands <b>$40B</b> fabrication plants in Arizona! https://news.example.com/tsmc #semiconductors</p>"
    cleaned = clean_text_for_display(raw_text)
    assert "<p>" not in cleaned
    assert "<b>" not in cleaned
    assert "TSMC expands $40B fabrication plants" in cleaned

    normalized = normalize_text_for_matching(raw_text)
    assert "https" not in normalized
    assert "#semiconductors" not in normalized
    assert normalized == "breaking tsmc expands 40b fabrication plants in arizona"


def test_extract_shingles():
    text = "semiconductor manufacturing"
    char_shingles = extract_shingles(text, k=3, word_level=False)
    assert len(char_shingles) > 0
    assert "sem" in char_shingles
    assert "con" in char_shingles

    word_shingles = extract_shingles(text, k=2, word_level=True)
    assert "semiconductor manufacturing" in word_shingles


# --- MinHash and LSH Tests ---

def test_minhash_jaccard_estimation():
    text1 = "The federal government passed new semiconductor subsidies to boost domestic chip production."
    text2 = "The federal government passed newer semiconductor subsidies to boost domestic chip fabrication."
    text3 = "Unrelated cooking recipe with tomatoes, olive oil, and garlic pasta for dinner tonight."

    m1 = MinHash(num_perm=128).update(extract_shingles(text1, k=3))
    m2 = MinHash(num_perm=128).update(extract_shingles(text2, k=3))
    m3 = MinHash(num_perm=128).update(extract_shingles(text3, k=3))

    sim_1_2 = m1.jaccard(m2)
    sim_1_3 = m1.jaccard(m3)

    assert sim_1_2 >= 0.70
    assert sim_1_3 <= 0.15


def test_minhash_lsh_duplicate_detection():
    lsh = MinHashLSH(threshold=0.60, num_perm=128)

    doc_a = "Nvidia reports record quarterly earnings driven by surging artificial intelligence data center demand."
    doc_b = "Nvidia reported record quarterly earnings driven by surging AI data center chip demand."
    doc_c = "Local weather forecast predicts heavy snowfall and freezing temperatures across the Midwest."

    m_a = MinHash(num_perm=128).update(extract_shingles(doc_a, k=3))
    m_b = MinHash(num_perm=128).update(extract_shingles(doc_b, k=3))
    m_c = MinHash(num_perm=128).update(extract_shingles(doc_c, k=3))

    lsh.insert("doc_a", m_a)
    lsh.insert("doc_c", m_c)

    matches_b = lsh.query(m_b)
    assert "doc_a" in matches_b
    assert "doc_c" not in matches_b


# --- Bot & Spam Detection Tests ---

def test_bot_detector():
    detector = BotDetector(min_char_length=20, min_word_count=4)

    # Legitimate news/opinion
    valid_item = CombinedRawData(
        slug_id=1,
        source="reddit",
        text_content="The new policy will significantly affect domestic semiconductor fabrication timeline over the next five years.",
        author_handle="u/tech_analyst",
    )
    res_valid = detector.evaluate(valid_item)
    assert not res_valid.is_bot

    # Spam keyword trigger
    spam_item = CombinedRawData(
        slug_id=1,
        source="reddit",
        text_content="Join our telegram group for free crypto airdrop signals and 100x gem alerts today!",
        author_handle="u/crypto_promoter",
    )
    res_spam = detector.evaluate(spam_item)
    assert res_spam.is_bot
    assert any("Spam keyword" in r for r in res_spam.reasons)

    # Bot author handle
    bot_author_item = CombinedRawData(
        slug_id=1,
        source="reddit",
        text_content="Daily discussion thread for tech news and market updates across global stock indices.",
        author_handle="u/AutoModerator",
    )
    res_bot = detector.evaluate(bot_author_item)
    assert res_bot.is_bot
    assert any("Bot author" in r for r in res_bot.reasons)

    # Excessive link spam
    link_spam_item = CombinedRawData(
        slug_id=1,
        source="reddit",
        text_content="Check this out: https://spam1.com/deal https://spam2.com/promo https://spam3.com/ref",
        author_handle="u/link_sharer",
    )
    res_link = detector.evaluate(link_spam_item)
    assert res_link.is_bot

    # Too short
    short_item = CombinedRawData(
        slug_id=1,
        source="reddit",
        text_content="Too short.",
        author_handle="u/user123",
    )
    res_short = detector.evaluate(short_item)
    assert res_short.is_bot


# --- Cross-Source Deduplication & Volume Gate Tests ---

def test_cross_source_deduplication_and_bot_flagging(db_session, sample_topic):
    # Cross-source items:
    # 1. Google News article A
    # 2. Reddit post duplicate of Article A (exact duplicate across sources)
    # 3. X tweet near-duplicate of Article A (near duplicate across sources)
    # 4. Google News article B (unique)
    # 5. X tweet C (unique)
    # 6. Bot spam post on Reddit (flagged)

    items = [
        CombinedRawData(
            slug_id=sample_topic.id,
            source="google_news",
            text_content="TSMC announces thirty billion investment in cutting-edge semiconductor fabrication facilities worldwide.",
            author_handle="Reuters",
            url="https://news.google.com/1",
        ),
        CombinedRawData(
            slug_id=sample_topic.id,
            source="reddit",
            text_content="TSMC announces thirty billion investment in cutting-edge semiconductor fabrication facilities worldwide.",
            author_handle="u/user1",
            url="https://reddit.com/r/1",
        ),
        CombinedRawData(
            slug_id=sample_topic.id,
            source="x",
            text_content="TSMC announced a thirty billion investment in cutting-edge semiconductor fabrication facilities worldwide!",
            author_handle="@user2",
            url="https://x.com/user2/1",
        ),
        CombinedRawData(
            slug_id=sample_topic.id,
            source="google_news",
            text_content="Intel reveals latest generation microprocessors with advanced neural processing units for laptop computing.",
            author_handle="Bloomberg",
            url="https://news.google.com/2",
        ),
        CombinedRawData(
            slug_id=sample_topic.id,
            source="x",
            text_content="Engineers debate the cooling efficiency and thermal architecture of high performance GPU clusters.",
            author_handle="@hardware_geek",
            url="https://x.com/hardware_geek/1",
        ),
        CombinedRawData(
            slug_id=sample_topic.id,
            source="reddit",
            text_content="Free crypto airdrop presale live now join telegram for discount promo codes!",
            author_handle="u/crypto_bot",
            url="https://reddit.com/r/spam",
        ),
    ]
    for it in items:
        db_session.add(it)
    db_session.commit()

    processor = DiscourseProcessor(min_volume_threshold=3, jaccard_threshold=0.70)
    result = processor.process_topic_items(topic=sample_topic, db=db_session)

    stats = result.statistics
    assert stats.total_raw_items == 6
    assert stats.bot_flagged_count == 1
    assert stats.exact_duplicates_removed == 1
    assert stats.near_duplicates_removed == 1
    assert stats.usable_items_count == 3
    assert stats.volume_gate_passed is True

    # Check that bot item was flagged in DB without deleting records
    all_db_items = db_session.query(CombinedRawData).filter(CombinedRawData.slug_id == sample_topic.id).all()
    assert len(all_db_items) == 6
    flagged = [it for it in all_db_items if it.is_flagged_bot]
    assert len(flagged) == 1
    assert flagged[0].author_handle == "u/crypto_bot"


def test_minimum_volume_gate(db_session, sample_topic):
    db_session.add(
        CombinedRawData(
            slug_id=sample_topic.id,
            source="reddit",
            text_content="First comprehensive analysis on international port congestion and shipping logistics across Asia.",
            author_handle="u/analyst_one",
        )
    )
    db_session.add(
        CombinedRawData(
            slug_id=sample_topic.id,
            source="google_news",
            text_content="Second detailed report on raw silicon supply constraints impacting European automotive manufacturing.",
            author_handle="Financial Times",
        )
    )
    db_session.commit()

    processor = DiscourseProcessor(min_volume_threshold=30)
    result = processor.process_topic_items(topic=sample_topic, db=db_session)

    assert result.statistics.usable_items_count == 2
    assert result.statistics.volume_gate_threshold == 30
    assert result.statistics.volume_gate_passed is False
