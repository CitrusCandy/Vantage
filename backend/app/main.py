from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.topics import router as topics_router
from app.api.workers import router as workers_router
from app.database.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database schema is initialized on startup
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Vantage News API",
    description="Multi-perspective real-time news clustering and synthesis API",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for frontend client communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
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


@app.get("/health")
def health_check():
    return {"status": "healthy"}


