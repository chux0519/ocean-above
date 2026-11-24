# syntax=docker/dockerfile:1
# api: FastAPI, single uvicorn worker (per-conversation locks are in-process; see docs/deploy.md).
FROM python:3.12-slim-bookworm

# uv pinned to the version used locally (uv 0.10.x); the lock file is used as-is (--frozen).
COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    PYDANTIC_AI_NO_BANNER=1

WORKDIR /app

# Dependencies first (cached layer). The project itself is not installed: pyproject sets `package = false`.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app

# locations.db is mounted read-only from the host directory ./locations (see docker-compose.yml).
ENV LOCATIONS_DB=/srv/locations/locations.db
EXPOSE 8000

# Single worker on purpose: conversation locks and the booking nonce store live in this process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
