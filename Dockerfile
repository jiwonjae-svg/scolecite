# =============================================================================
# Project Scolecite - Dockerfile (Cloud Run / Local)
# DISCLAIMER: For educational/research purposes only.
# =============================================================================

FROM python:3.12-slim

# Prevent Python from buffering stdout/stderr (Cloud Run logging)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY shared/ ./shared/
COPY server/ ./server/

# Expose port (Cloud Run uses PORT env var, default 8000)
ENV PORT=8000
EXPOSE ${PORT}

# Entry point: run only the FastAPI server
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT}"]
