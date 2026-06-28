# ─────────────────────────────────────────────────────────────────
# Enterprise Revenue Recovery Agent — Dockerfile
# Phase 5: Production Container for Google Cloud Run
# ─────────────────────────────────────────────────────────────────

# ── Stage 1: Build dependencies ──────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build tools + libpq (required by asyncpg/psycopg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for Docker layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime image ────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install libpq runtime dependency only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source code
COPY src/ ./src/
COPY db/ ./db/
COPY pyproject.toml .

# Create non-root user for security (STRIDE: Elevation of Privilege mitigation)
RUN useradd -m -u 1000 agentuser && chown -R agentuser:agentuser /app
USER agentuser

# Cloud Run injects PORT env var — default to 8080
ENV PORT=8080

# Expose the port
EXPOSE 8080

# Production entrypoint — FastAPI via Uvicorn
CMD ["python", "-m", "uvicorn", "src.web.app:app", \
    "--host", "0.0.0.0", \
    "--port", "8080", \
    "--workers", "2", \
    "--log-level", "info"]
