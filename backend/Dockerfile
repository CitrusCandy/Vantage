# Production Dockerfile for Vantage News Backend (FastAPI)
FROM python:3.10-slim as base

# Environment settings
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

# Install system runtime dependencies (including PostgreSQL client tools for backup/restore)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    postgresql-client \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy backend source code
COPY . .

# Create non-root system user for security and initialize backup directory
RUN useradd -m -u 1001 appuser && \
    mkdir -p /app/backups && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Container healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default launch command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
