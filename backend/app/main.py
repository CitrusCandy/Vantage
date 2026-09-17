from contextlib import asynccontextmanager
import logging
import os
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.ops import router as ops_router
from app.api.topics import router as topics_router
from app.api.workers import router as workers_router
from app.core.metrics import platform_metrics
from app.core.security import get_allowed_cors_origins, mask_sensitive_data
from app.core.slo import slo_manager
from app.database.database import Base, engine

logger = logging.getLogger("app.main")
http_logger = logging.getLogger("app.http")

# Maximum permitted HTTP request payload size (2MB)
MAX_REQUEST_BODY_SIZE = 2 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database schema is initialized on startup
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        logger.warning("Database startup init skipped: %s", mask_sensitive_data(str(e)))
    yield


app = FastAPI(
    title="Vantage News API",
    description="Multi-perspective real-time news clustering and synthesis API",
    version="1.0.0",
    lifespan=lifespan,
)


# Enable secure CORS for frontend client communication
allowed_origins = get_allowed_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True if "*" not in allowed_origins else False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-API-Key",
        "X-Ops-Key",
        "X-Admin-Key",
        "Accept",
        "Origin",
        "User-Agent",
    ],
)


def _normalize_http_path(path: str) -> str:
    """Normalize URL paths to bounded low-cardinality label values."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    if parts[0] == "api" and len(parts) > 1:
        if parts[1] == "topics":
            if len(parts) >= 3 and parts[2] in ("trending", "search", "categories"):
                return f"/api/topics/{parts[2]}"
            elif len(parts) >= 3:
                return "/api/topics/{slug}"
            return "/api/topics"
        if parts[1] in ("ops", "workers"):
            return f"/api/{parts[1]}/{parts[2]}" if len(parts) >= 3 else f"/api/{parts[1]}"
    return "/" + "/".join(parts[:2])


@app.middleware("http")
async def security_and_telemetry_middleware(request: Request, call_next):
    # 1. Enforce Request Body Size Limits (HTTP 413 Payload Too Large)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            cl_int = int(content_length)
            if cl_int > MAX_REQUEST_BODY_SIZE:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": f"Payload too large. Maximum allowed size is {MAX_REQUEST_BODY_SIZE // (1024 * 1024)}MB.",
                    },
                )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header."},
            )

    start_time = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        safe_msg = mask_sensitive_data(str(exc))
        http_logger.error(
            "[HTTP_ERROR] %s %s -> Exception: %s (%.2f ms)",
            request.method,
            request.url.path,
            safe_msg,
            duration_ms,
        )
        # Return generic sanitized JSON 500 without leaking stack traces or internal secrets
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal server error occurred."},
        )

    duration_ms = (time.perf_counter() - start_time) * 1000.0

    # 2. Add Comprehensive Security & Telemetry Headers
    response.headers["X-Process-Time"] = f"{duration_ms:.2f}ms"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; font-src 'self' https:; connect-src 'self' https:; "
        "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"

    # 3. Record Low-Cardinality HTTP Structured Metrics
    norm_path = _normalize_http_path(request.url.path)
    status_group = f"{response.status_code // 100}xx"
    counter = platform_metrics.get_counter("http_requests_total")
    if counter:
        counter.inc(labels={"method": request.method, "path": norm_path, "status": status_group})
    hist = platform_metrics.get_histogram("http_request_duration_ms")
    if hist:
        hist.observe(duration_ms, labels={"method": request.method, "path": norm_path})

    http_logger.info(
        "[HTTP] %s %s -> %d (%.2f ms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


# Include application routers
app.include_router(topics_router, prefix="/api")
app.include_router(workers_router, prefix="/api")
app.include_router(ops_router, prefix="/api")


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics():
    """Prometheus exposition metrics endpoint."""
    return PlainTextResponse(
        platform_metrics.generate_prometheus_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


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
        logger.error("Readiness database ping failed: %s", mask_sensitive_data(str(e)))
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
