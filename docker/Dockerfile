# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.9.22 AS uv

FROM python:3.13-slim AS builder

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Keep third-party dependencies in a layer that changes only when the lockfile
# changes. The BuildKit cache persists uv downloads between builds.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,id=malg-uv-cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

COPY default.config.toml ./
COPY README.md ./
COPY malg ./malg
RUN --mount=type=cache,id=malg-uv-cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

FROM python:3.13-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The Compose file further removes every Linux capability and makes the image
# filesystem read-only at runtime.
RUN groupadd --gid 65532 malg \
    && useradd --uid 65532 --gid 65532 --create-home --home-dir /home/malg --shell /usr/sbin/nologin malg

COPY --from=builder --chown=65532:65532 /app/.venv /app/.venv
COPY --from=builder --chown=65532:65532 /app/default.config.toml ./
COPY --from=builder --chown=65532:65532 /app/malg ./malg

USER 65532:65532

CMD ["python", "-m", "malg"]
