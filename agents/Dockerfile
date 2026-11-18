# syntax=docker/dockerfile:1
#
# Stage order matters. `docker build` with no --target builds the LAST stage, so
# the runtime image must come last; otherwise `docker build agents/` quietly
# yields the test image, whose CMD is pytest.

FROM python:3.12-slim AS base

# Pinned to the same uv that generated uv.lock, so the lock format cannot skew.
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Own the trees before anything lands in them, then build as appuser. A later
# `chown -R` would be an ownership-only change that OverlayFS still has to
# copy up, duplicating the whole venv in a second layer.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /opt/venv \
    && chown appuser:appuser /opt/venv /app
USER appuser

# Dependencies resolve in their own layer so editing source does not reinstall.
# --frozen installs exactly what uv.lock pins, so the image matches a fresh clone.
COPY --chown=appuser:appuser pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY --chown=appuser:appuser src ./src
COPY --chown=appuser:appuser prompts ./prompts
RUN uv sync --frozen --no-dev

# Test stage adds the dev dependency group. The runtime image below never
# carries pytest, ruff or mypy.
FROM base AS test
RUN uv sync --frozen
COPY --chown=appuser:appuser tests ./tests
CMD ["pytest"]

FROM base AS runtime
# Already non-root from `base`. Keys arrive via env_file at run time and are
# never baked in (§9).

EXPOSE 8000

# Liveness only: a Postgres outage must not make Compose restart a healthy
# process. Gate dependent services on /ready instead.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"

CMD ["python", "-m", "offerradar_agents.api"]
