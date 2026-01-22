# =============================================================================
# Sentiment Intelligence Platform - Dockerfile
# =============================================================================
# Multi-stage build for smaller, production-ready image
# - Builder stage installs dependencies into a virtualenv
# - Runtime stage copies only the venv + app code
# - TextBlob corpora baked into image (NLTK_DATA set)
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Builder
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /app

# Build deps for compiling wheels (e.g., psycopg2) and Postgres headers
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
 && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest first for better caching
COPY requirements.txt ./

# Create a virtualenv and install dependencies into it
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Download TextBlob corpora into default nltk_data location (under /root/nltk_data)
RUN /opt/venv/bin/python -m textblob.download_corpora


# -----------------------------------------------------------------------------
# Stage 2: Runtime (Production)
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime deps only (Postgres client lib + curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
 && rm -rf /var/lib/apt/lists/*

# Copy the virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy TextBlob/NLTK corpora into a shared, non-root-friendly location
COPY --from=builder /root/nltk_data /usr/local/share/nltk_data
ENV NLTK_DATA=/usr/local/share/nltk_data

# Copy application code
COPY app/ ./app/

# Create non-root user and ensure permissions
RUN useradd -m -u 1000 appuser \
 && chown -R appuser:appuser /app

USER appuser

# Healthcheck (expects your Flask health blueprint at /healthz)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

# Default command (can be overridden in docker-compose for worker)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "app:create_app()"]
