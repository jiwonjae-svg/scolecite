# =============================================================================
# Project Scolecite - Dockerfile (Oracle Cloud VPS)
# DISCLAIMER: For educational/research purposes only.
# =============================================================================
# Optimised for Oracle Cloud Always Free Ampere A1 (ARM64):
#   - Multi-stage build for smaller image
#   - Non-root user for security
#   - Gunicorn + Uvicorn workers for production concurrency
#   - PostgreSQL-ready (asyncpg)
#   - Works on both ARM64 (OCI A1) and AMD64 (local dev)
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

# Prevent Python from buffering stdout/stderr (important for journalctl logging)
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
# settings.json: use example if missing (runtime mount overrides; app creates defaults)
COPY settings.json.example ./settings.json

# Create dirs for backups & data
RUN mkdir -p /app/backups /app/data && chown -R scolecite:scolecite /app

USER scolecite

# Default port
ENV PORT=8000
EXPOSE ${PORT}

# Health check for docker-compose and monitoring
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Production entry: gunicorn w/ uvicorn workers
# Oracle A1 has 4 OCPUs → 4 workers for optimal concurrency
CMD ["sh", "-c", "gunicorn server.main:app \
    --bind 0.0.0.0:${PORT} \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers ${GUNICORN_WORKERS:-4} \
    --timeout 120 \
    --graceful-timeout 30 \
    --keep-alive 65 \
    --access-logfile - \
    --error-logfile -"]
