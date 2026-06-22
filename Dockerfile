# syntax=docker/dockerfile:1.7
# Multi-stage build for the gene-target validation system.
#
# Build targets (match docker-compose.yml service targets):
#   mcp-servers      — runs DB migrations; MCP tools are loaded in-process by agents
#   agents-knowledge — A2A server for data-acquisition + processing agents
#   agents-reasoning — A2A server for reasoning agents
#   report-agent     — A2A server for the report agent (needs writable /app/results/)
#   planner          — user-facing REST API + in-process LangGraph orchestration
#   chat             — Gradio assistant (MCP gateway client); needs the mcp-gateway service
#
# All non-planner A2A services require the planner to be refactored for
# cross-container dispatch; they are scaffolded here for that future work.

ARG PYTHON_VERSION=3.12

# ─────────────────────────────────────────────────────────────────────────────
# builder: install Python deps via uv into a virtual environment
# ─────────────────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

# System packages needed to build psycopg (libpq) and cryptography wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv from the official distribution image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency manifest first — this layer is cache-friendly; it only
# rebuilds when pyproject.toml or uv.lock changes.
COPY pyproject.toml uv.lock ./

# Install production dependencies into a venv at /app/.venv.
# --no-install-project: don't install the project itself yet (source not copied).
# --frozen: fail if uv.lock is out of date rather than silently updating.
RUN uv sync --frozen --no-dev --no-install-project

# ─────────────────────────────────────────────────────────────────────────────
# base: minimal runtime image with deps + full source tree
# ─────────────────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS base

# Runtime system packages (libpq for psycopg, curl for healthchecks)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        tesseract-ocr \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy the pre-built venv from the builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy source (all packages share the same layout)
COPY pyproject.toml uv.lock ./
COPY src/     ./src/
COPY skills/  ./skills/
COPY config/  ./config/
COPY alembic.ini  ./

# Install the project itself into the existing venv (no dep resolution needed)
RUN uv sync --frozen --no-dev

# Use the venv Python by default; add venv bin to PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Non-root user for all services
RUN groupadd --gid 1001 app && useradd --uid 1001 --gid app --no-create-home app
USER app

# ─────────────────────────────────────────────────────────────────────────────
# mcp-servers: one-shot DB migration runner
#
# MCP tools are imported in-process by agents; no persistent daemon is needed.
# docker-compose runs this as an init-style service (restartPolicy: no).
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS mcp-servers

# Must run as root to write to the alembic version table the first time
USER root
CMD ["alembic", "upgrade", "head"]

# ─────────────────────────────────────────────────────────────────────────────
# agents-knowledge: A2A server for data-acquisition + processing agents
#
# Handles: literature, patent, clinical_trial, opentargets, genetics, omics,
#          screening, knowledge_extraction
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS agents-knowledge

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fk --cacert /certs/ca/ca.crt --cert /certs/services/service.crt --key /certs/services/service.key https://localhost:8001/health || exit 1

CMD ["python", "-m", "core.a2a.run_service", "--service", "knowledge", "--port", "8001"]

# ─────────────────────────────────────────────────────────────────────────────
# agents-reasoning: A2A server for experiment, critic, and reviewer agents
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS agents-reasoning

EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fk --cacert /certs/ca/ca.crt --cert /certs/services/service.crt --key /certs/services/service.key https://localhost:8002/health || exit 1

CMD ["python", "-m", "core.a2a.run_service", "--service", "reasoning", "--port", "8002"]

# ─────────────────────────────────────────────────────────────────────────────
# report-agent: A2A server for the report agent
#
# Requires writable /app/results/ volumes (mounted by docker-compose).
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS report-agent

USER root
RUN mkdir -p /app/results/data /app/results/original /app/results/experiment /app/results/report \
    && chown -R app:app /app/results
USER app

VOLUME ["/app/results/report"]
EXPOSE 8003
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fk --cacert /certs/ca/ca.crt --cert /certs/services/service.crt --key /certs/services/service.key https://localhost:8003/health || exit 1

CMD ["python", "-m", "core.a2a.run_service", "--service", "report", "--port", "8003"]

# ─────────────────────────────────────────────────────────────────────────────
# planner: user-facing REST API + in-process LangGraph orchestration
#
# This is the primary target for the current single-container deployment.
# It runs all agents in-process via build_graph.py and exposes port 8000.
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS planner

USER root
RUN mkdir -p /app/results/data /app/results/original /app/results/experiment /app/results/report \
    && chown -R app:app /app/results
USER app

VOLUME ["/app/results/report"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
    CMD curl -f http://localhost:8000/docs || exit 1

# Single worker — LangGraph's in-process state is not safe to share across
# multiple OS processes.  Use async concurrency (uvicorn's event loop) instead.
CMD ["uvicorn", "agents.planner.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1"]

# ─────────────────────────────────────────────────────────────────────────────
# chat: Gene Target Validation Assistant — Gradio chat over the MCP gateway
#
# A user-facing client of the gateway (not the pipeline). Talks to the separate
# mcp-gateway HTTP service via MCP_GATEWAY_URL and persists conversation state in
# Postgres. The `chat` dependency group (gradio, langchain-ollama) is excluded by
# the base stage's --no-dev, so it is installed explicitly here.
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS chat

USER root
RUN uv sync --frozen --no-dev --group chat
USER app

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
    CMD curl -f http://localhost:7860/ || exit 1

CMD ["atv-chat"]
