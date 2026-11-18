# syntax=docker/dockerfile:1
# Runtime stage must be last: `docker build` with no --target builds the final stage.

FROM python:3.12-slim AS base

# Pinned to the uv that generated uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# chown before the copies, not after: a later `chown -R` copies up the whole venv as a new layer.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /opt/venv \
    && chown appuser:appuser /opt/venv /app
USER appuser

# Own layer so editing source does not reinstall. --frozen installs exactly uv.lock.
COPY --chown=appuser:appuser pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY --chown=appuser:appuser src ./src
COPY --chown=appuser:appuser prompts ./prompts
RUN uv sync --frozen --no-dev

FROM base AS test
RUN uv sync --frozen
COPY --chown=appuser:appuser tests ./tests
CMD ["pytest"]

FROM base AS runtime

EXPOSE 8000

# Liveness only: gate dependent services on /ready, not this.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"

CMD ["python", "-m", "offerradar_agents.api"]
