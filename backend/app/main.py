from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.ops import router as ops_router
from app.api.topics import router as topics_router
from app.api.workers import router as workers_router
from app.database.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database schema is initialized on startup
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        logging.getLogger("app.main").warning("Database startup init skipped: %s", e)
    yield


app = FastAPI(
    title="Vantage News API",
    description="Multi-perspective real-time news clustering and synthesis API",
    version="1.0.0",
    lifespan=lifespan,
)

from app.core.security import get_allowed_cors_origins

# Enable secure CORS for frontend client communication
allowed_origins = get_allowed_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True if "*" not in allowed_origins else False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

import logging
import time
from fastapi import Request

logger = logging.getLogger("app.http")


@app.middleware("http")
async def add_process_time_and_log_middleware(request: Request, call_next):
    start_time = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start_time) * 1000.0
    response.headers["X-Process-Time"] = f"{duration_ms:.2f}ms"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    logger.info(
        "[HTTP] %s %s -> %d (%.2f ms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


app.include_router(topics_router, prefix="/api")
app.include_router(workers_router, prefix="/api")
app.include_router(ops_router, prefix="/api")


@app.get("/health")
def health_check():
    """Liveness probe: verifies process is alive."""
    return {"status": "healthy"}


@app.get("/ready")
def readiness_check():
    """Readiness probe: verifies database connectivity and core services."""
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        return {"status": "unready", "database": "disconnected", "detail": str(e)}, 503



