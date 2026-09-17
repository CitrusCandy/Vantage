from datetime import datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.database import Base
from app.database.models import Topic


@pytest.fixture(autouse=True)
def setup_test_database(monkeypatch):
    """Autouse fixture providing fast, isolated in-memory SQLite storage for all tests.

    Ensures SessionLocal calls in telemetry, alerting, and workers write to memory
    without hanging on PostgreSQL TCP connect timeouts when running offline.
    """
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    monkeypatch.setattr("app.database.database.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.database.database.engine", test_engine)

    try:
        from app.core.metrics import platform_metrics
        from app.core.resilience import circuit_registry
        platform_metrics.reset_all()
        circuit_registry.reset_all()
    except Exception:
        pass

    yield

    try:
        from app.core.metrics import platform_metrics
        from app.core.resilience import circuit_registry
        platform_metrics.reset_all()
        circuit_registry.reset_all()
    except Exception:
        pass

    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session():
    """Create a database session using the active test engine."""
    from app.database import database
    session = database.SessionLocal()
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
