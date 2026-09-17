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


# ==========================================
# Persistent Operational History & Telemetry
# ==========================================


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    run_id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=True, index=True)
    topic_slug = Column(String(255), nullable=True, index=True)
    pipeline_type = Column(String(100), nullable=False, default="discourse_pipeline")
    status = Column(String(50), nullable=False, default="success")  # success, failed, running
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Float, nullable=False, default=0.0)
    sample_size = Column(Integer, default=0, nullable=True)
    cluster_count = Column(Integer, default=0, nullable=True)
    perspective_count = Column(Integer, default=0, nullable=True)
    failure_stage = Column(String(100), nullable=True)
    error_type = Column(String(255), nullable=True)
    stages_ms = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_pipeline_runs_status_started", "status", "started_at"),
        Index("ix_pipeline_runs_slug_started", "topic_slug", "started_at"),
    )

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "topic_id": self.topic_id,
            "topic_slug": self.topic_slug,
            "pipeline_type": self.pipeline_type,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": round(self.duration_ms, 2),
            "sample_size": self.sample_size,
            "cluster_count": self.cluster_count,
            "perspective_count": self.perspective_count,
            "failure_stage": self.failure_stage,
            "error_type": self.error_type,
            "stages_ms": self.stages_ms or {},
        }


class SourceExecution(Base):
    __tablename__ = "source_executions"

    execution_id = Column(Integer, primary_key=True, index=True)
    source = Column(String(50), nullable=False, index=True)  # google_news, reddit, x, openai
    operation = Column(String(100), nullable=False, default="fetch")  # fetch, scrape, embeddings, synthesis
    status = Column(String(50), nullable=False, default="success")  # success, failed, timeout
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Float, nullable=False, default=0.0)
    item_count = Column(Integer, default=0, nullable=True)
    error_type = Column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_source_executions_src_status_started", "source", "status", "started_at"),
    )

    def to_dict(self):
        return {
            "execution_id": self.execution_id,
            "source": self.source,
            "operation": self.operation,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": round(self.duration_ms, 2),
            "item_count": self.item_count,
            "error_type": self.error_type,
        }


class WorkerCycle(Base):
    __tablename__ = "worker_cycles"

    cycle_id = Column(Integer, primary_key=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Float, nullable=False, default=0.0)
    topics_considered = Column(Integer, default=0, nullable=False)
    topics_refreshed = Column(Integer, default=0, nullable=False)
    topics_skipped = Column(Integer, default=0, nullable=False)
    topics_failed = Column(Integer, default=0, nullable=False)
    status = Column(String(50), nullable=False, default="success")  # success, failed
    error_summary = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_worker_cycles_status_started", "status", "started_at"),
    )

    def to_dict(self):
        return {
            "cycle_id": self.cycle_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": round(self.duration_ms, 2),
            "topics_considered": self.topics_considered,
            "topics_refreshed": self.topics_refreshed,
            "topics_skipped": self.topics_skipped,
            "topics_failed": self.topics_failed,
            "status": self.status,
            "error_summary": self.error_summary,
        }


class OperationalAlert(Base):
    __tablename__ = "operational_alerts"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(String(150), unique=True, index=True, nullable=False)  # rule_name:component
    alert_type = Column(String(100), nullable=False, index=True)  # rule_name
    severity = Column(String(50), nullable=False, index=True)  # info, warning, critical
    component = Column(String(100), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="active", index=True)  # active, resolved
    message = Column(Text, nullable=False)
    occurrence_count = Column(Integer, default=1, nullable=False)
    first_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    metadata_json = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_operational_alerts_status_severity", "status", "severity"),
        Index("ix_operational_alerts_last_seen", "last_seen"),
    )

    def to_dict(self):
        return {
            "id": self.alert_id,
            "rule_name": self.alert_type,
            "severity": self.severity,
            "component": self.component,
            "status": self.status,
            "message": self.message,
            "occurrence_count": self.occurrence_count,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "metadata": self.metadata_json or {},
        }


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id = Column(Integer, primary_key=True, index=True)
    backup_id = Column(String(100), unique=True, index=True, nullable=False)
    filename = Column(String(255), nullable=False)
    filepath = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(50), nullable=False, default="running", index=True)  # running, success, failed, verified, corrupted
    size_bytes = Column(Integer, default=0, nullable=False)
    checksum = Column(String(64), nullable=True)  # SHA-256
    database_name = Column(String(100), nullable=True)
    schema_version = Column(String(50), default="2.0.0", nullable=False)
    error_type = Column(Text, nullable=True)
    is_verified = Column(Boolean, default=False, nullable=False)
    verified_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_backup_records_status_created", "status", "created_at"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "backup_id": self.backup_id,
            "filename": self.filename,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "status": self.status,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "database_name": self.database_name,
            "schema_version": self.schema_version,
            "error_type": self.error_type,
            "is_verified": self.is_verified,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
        }


class SLOViolationRecord(Base):
    __tablename__ = "slo_violation_records"

    id = Column(Integer, primary_key=True, index=True)
    slo_name = Column(String(100), nullable=False, index=True)
    status = Column(String(50), nullable=False, index=True)  # WARNING, VIOLATED, RESOLVED
    target_value = Column(Float, nullable=False)
    actual_value = Column(Float, nullable=False)
    burn_rate = Column(Float, nullable=False, default=0.0)
    error_budget_remaining_percent = Column(Float, nullable=False, default=100.0)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    resolved_at = Column(DateTime, nullable=True)
    details_json = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_slo_violations_name_started", "slo_name", "started_at"),
        Index("ix_slo_violations_status_started", "status", "started_at"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "slo_name": self.slo_name,
            "status": self.status,
            "target_value": self.target_value,
            "actual_value": round(self.actual_value, 4),
            "burn_rate": round(self.burn_rate, 2),
            "error_budget_remaining_percent": round(self.error_budget_remaining_percent, 2),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "details": self.details_json or {},
        }


class SecurityAuditLog(Base):
    __tablename__ = "security_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    actor = Column(String(100), nullable=False, default="anonymous", index=True)
    role = Column(String(50), nullable=False, default="public", index=True)
    action = Column(String(100), nullable=False, index=True)  # auth_failure, authorization_denied, backup_created, config_changed, etc.
    resource = Column(String(255), nullable=True, index=True)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False, default="allowed", index=True)  # allowed, denied, failed, error
    details_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_security_audit_action_timestamp", "action", "timestamp"),
        Index("ix_security_audit_status_timestamp", "status", "timestamp"),
        Index("ix_security_audit_actor_timestamp", "actor", "timestamp"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "actor": self.actor,
            "role": self.role,
            "action": self.action,
            "resource": self.resource,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "status": self.status,
            "details": self.details_json or {},
            "error_message": self.error_message,
        }




