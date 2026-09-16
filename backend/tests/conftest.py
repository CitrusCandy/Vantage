import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.database import Base


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
    yield
    Base.metadata.drop_all(bind=test_engine)
