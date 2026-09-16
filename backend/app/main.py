from fastapi import FastAPI

from app.api.topics import router as topics_router

app = FastAPI(
    title="Vantage News API",
    description="Multi-perspective real-time news clustering and synthesis API",
    version="1.0.0",
)

app.include_router(topics_router, prefix="/api")


@app.get("/health")
def health_check():
    return {"status": "healthy"}
