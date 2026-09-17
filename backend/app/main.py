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


from fastapi.responses import JSONResponse


@app.get("/health")
def health_check():
    """Liveness probe: verifies process is alive."""
    return {"status": "healthy", "service": "vantage-news-api"}


@app.get("/ready")
def readiness_check():
    """Readiness probe: verifies database connectivity, shared governance, and circuit breaker states."""
    from app.core.resilience import circuit_registry
    from app.core import resource_governor

    # 1. Database Check
    db_connected = False
    try:
        from sqlalchemy import text
        from app.database import database
        with database.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_connected = True
    except Exception as e:
        logger.error("Readiness database ping failed: %s", str(e))
        db_connected = False

    if not db_connected:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unready",
                "database": "disconnected",
                "reason": "Database connection unavailable",
            },
        )

    # 2. Governance & Redis Status
    backend_info = resource_governor.coordinator.get_backend_info()

    # 3. Circuit Breakers Snapshot
    breakers_status = circuit_registry.get_all_status()
    open_breakers = [name for name, s in breakers_status.items() if s.get("state") == "OPEN"]
    half_open_breakers = [name for name, s in breakers_status.items() if s.get("state") == "HALF_OPEN"]

    # 4. Determine Overall Readiness Status
    is_degraded = bool(open_breakers or half_open_breakers or backend_info.get("fallback_active"))
    overall_status = "degraded" if is_degraded else "ready"

    return {
        "status": overall_status,
        "database": "connected",
        "governance": {
            "active_backend": backend_info.get("backend"),
            "is_healthy": backend_info.get("is_healthy"),
            "fallback_active": backend_info.get("fallback_active"),
        },
        "circuit_breakers": {
            "all_healthy": len(open_breakers) == 0 and len(half_open_breakers) == 0,
            "open": open_breakers,
            "half_open": half_open_breakers,
        },
    }



