# =============================================================================
# Project Scolecite - Dockerfile (Cloud Run / Local)
# DISCLAIMER: For educational/research purposes only.
# =============================================================================
# Optimised for Google Cloud Run:
#   - Multi-stage build for smaller image
#   - Non-root user for security
#   - Gunicorn + Uvicorn workers for production concurrency
#   - Cloud SQL Auth Proxy unix-socket ready
# =============================================================================

# ---------- Stage 1: builder ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim

# Prevent Python from buffering stdout/stderr (Cloud Run logging)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Non-root user for security
RUN groupadd -r scolecite && useradd -r -g scolecite -d /app -s /sbin/nologin scolecite

WORKDIR /app

# Runtime system deps (libpq for asyncpg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY shared/ ./shared/
COPY server/ ./server/
COPY settings.json* ./

# Create dirs for Cloud SQL socket & backups
RUN mkdir -p /cloudsql /app/backups && chown -R scolecite:scolecite /app /cloudsql

USER scolecite

# Cloud Run uses PORT env var (default 8000)
ENV PORT=8000
EXPOSE ${PORT}

# Health check (used by Cloud Run startup/liveness probes)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Production entry: gunicorn w/ uvicorn workers
# Cloud Run concurrency=80 → 2 workers × 40 connections each
CMD ["sh", "-c", "gunicorn server.main:app \
    --bind 0.0.0.0:${PORT} \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 \
    --timeout 120 \
    --graceful-timeout 30 \
    --keep-alive 65 \
    --access-logfile - \
    --error-logfile -"]
