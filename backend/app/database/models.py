from datetime import datetime
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
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

    raw_data = relationship("RawData", back_populates="topic", cascade="all, delete-orphan")
    perspectives = relationship("Perspective", back_populates="topic", cascade="all, delete-orphan")
    cluster_runs = relationship("ClusterRun", back_populates="topic", cascade="all, delete-orphan")


class RawData(Base):
    __tablename__ = "raw_data"

    raw_id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False, index=True)
    source = Column(String(100), nullable=False)
    text_content = Column(Text, nullable=False)
    url = Column(String(1024), nullable=True)
    author_handle = Column(String(255), nullable=True)
    engagement_metrics = Column(JSON, nullable=True)
    is_flagged_bot = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    topic = relationship("Topic", back_populates="raw_data")


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
