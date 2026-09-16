from datetime import datetime
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database.database import Base


class Topic(Base):
    __tablename__ = "topics"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, index=True, nullable=False)
    search_count = Column(Integer, default=0, nullable=False)
    trending_score = Column(Float, default=0.0, nullable=False)
    source_coverage = Column(JSON, nullable=True)
    last_clustered_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Staging Tables
    raw_google_news = relationship("RawGoogleNews", back_populates="topic", cascade="all, delete-orphan")
    raw_reddit = relationship("RawReddit", back_populates="topic", cascade="all, delete-orphan")
    raw_x = relationship("RawX", back_populates="topic", cascade="all, delete-orphan")

    # Unified Merged Dataset
    combined_raw_data = relationship("CombinedRawData", back_populates="topic", cascade="all, delete-orphan")

    # Downstream Analytics
    perspectives = relationship("Perspective", back_populates="topic", cascade="all, delete-orphan")
    cluster_runs = relationship("ClusterRun", back_populates="topic", cascade="all, delete-orphan")


class RawGoogleNews(Base):
    __tablename__ = "raw_google_news"

    id = Column(Integer, primary_key=True, index=True)
    slug_id = Column(Integer, ForeignKey("topics.id"), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    link = Column(String(1024), nullable=True)
    source_name = Column(String(255), nullable=True)
    published_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    snippet = Column(Text, nullable=True)

    topic = relationship("Topic", back_populates="raw_google_news")


class RawReddit(Base):
    __tablename__ = "raw_reddit"

    id = Column(Integer, primary_key=True, index=True)
    slug_id = Column(Integer, ForeignKey("topics.id"), nullable=False, index=True)
    post_id = Column(String(100), nullable=True)
    body = Column(Text, nullable=False)
    score = Column(Integer, default=0, nullable=False)
    num_comments = Column(Integer, default=0, nullable=False)
    subreddit = Column(String(100), nullable=True)
    author = Column(String(255), nullable=True)
    created_utc = Column(DateTime, default=datetime.utcnow, nullable=False)

    topic = relationship("Topic", back_populates="raw_reddit")


class RawX(Base):
    __tablename__ = "raw_x"

    id = Column(Integer, primary_key=True, index=True)
    slug_id = Column(Integer, ForeignKey("topics.id"), nullable=False, index=True)
    tweet_id = Column(String(100), nullable=True)
    text = Column(Text, nullable=False)
    likes = Column(Integer, default=0, nullable=False)
    retweets = Column(Integer, default=0, nullable=False)
    replies = Column(Integer, default=0, nullable=False)
    handle = Column(String(255), nullable=True)
    posted_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    topic = relationship("Topic", back_populates="raw_x")


class CombinedRawData(Base):
    __tablename__ = "combined_raw_data"

    raw_id = Column(Integer, primary_key=True, index=True)
    slug_id = Column(Integer, ForeignKey("topics.id"), nullable=False, index=True)
    source = Column(String(50), nullable=False)
    text_content = Column(Text, nullable=False)
    url = Column(String(1024), nullable=True)
    author_handle = Column(String(255), nullable=True)
    engagement_metrics = Column(JSON, nullable=True)
    is_flagged_bot = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_combined_slug_source_created", "slug_id", "source", "created_at"),
    )

    topic = relationship("Topic", back_populates="combined_raw_data")


# Backward compatibility alias
RawData = CombinedRawData


class Perspective(Base):
    __tablename__ = "perspectives"

    per_id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False, index=True)
    perspective_type = Column(String(100), nullable=False)
    estimated_share = Column(Float, nullable=True)
    summary_points = Column(JSON, nullable=True)
    sample_quotes = Column(JSON, nullable=True)
    confidence_note = Column(Text, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    topic = relationship("Topic", back_populates="perspectives")


class ClusterRun(Base):
    __tablename__ = "cluster_runs"

    run_id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False, index=True)
    cluster_algorithm = Column(String(100), nullable=False)
    cluster_count = Column(Integer, nullable=False)
    sample_size = Column(Integer, nullable=False)
    run_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    topic = relationship("Topic", back_populates="cluster_runs")
