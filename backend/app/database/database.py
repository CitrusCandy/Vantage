import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/vantage_news",
)

engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

from sqlalchemy import event
import time


@event.listens_for(engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("query_start_time", []).append(time.perf_counter())


@event.listens_for(engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    try:
        start_list = conn.info.get("query_start_time", [])
        if start_list:
            total = time.perf_counter() - start_list.pop()
            duration_ms = total * 1000.0
            from app.core.metrics import platform_metrics
            hist = platform_metrics.get_histogram("database_query_duration_ms")
            if hist:
                hist.observe(duration_ms)
    except Exception:
        pass


def get_db():
    """Dependency for providing a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

