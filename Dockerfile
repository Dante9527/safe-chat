# ============================================================================
# SafeChat Dockerfile — 2026 production best practices
# ============================================================================
# Multi-stage build + non-root user + slim runtime
# Reference: https://fastapi.tiangolo.com/deployment/docker/
# ============================================================================

# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ---------------------------------------------------------------------------
# Stage 1: Builder — install Python deps (needs build-essential for C extensions)
# ---------------------------------------------------------------------------
FROM base AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Layer 1: heavy / stable deps (sentence-transformers, chromadb, torch CPU)
# This layer is cached separately — adding a light package won't bust it.
COPY requirements-heavy.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --user -r requirements-heavy.txt

# Layer 2: application deps (change more often)
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --user -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: Runtime — slim, no build tools, non-root user
# ---------------------------------------------------------------------------
FROM base AS runtime

# Runtime deps only (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# --- Security: non-root user (2026 best practice) ---
RUN groupadd --system --gid 1001 appgroup && \
    useradd --system --uid 1001 --gid appgroup --create-home appuser

WORKDIR /app

# Copy Python packages from builder, owned by appuser
COPY --from=builder --chown=appuser:appgroup /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH

# Copy application code
COPY --chown=appuser:appgroup app/ ./app/
COPY --chown=appuser:appgroup templates/ ./templates/
COPY --chown=appuser:appgroup static/ ./static/
COPY --chown=appuser:appgroup scripts/ ./scripts/

# Create data dirs writable by appuser
RUN mkdir -p data/uploads data/chroma_db data/sample_docs && \
    chown -R appuser:appgroup data/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
