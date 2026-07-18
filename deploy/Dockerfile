# Multi-stage uv build of the lzt_eventus daemon. Non-root runtime.
# Build context is the repo root (see docker-compose.yml: context: ..).

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# git is needed at build time: the aiolzt SDK is a git dependency, so `uv sync`
# clones it during the build. The uv base image is slim and ships no git.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Resolve + install deps first (cached) using only the manifests.
COPY pyproject.toml uv.lock ./
# aiolzt is a private git dependency. A BuildKit secret (id=gh_token) carries a
# GitHub token; it's exported into git's env for the clone only (never written to
# a layer, so it can't leak into the image). Optional: without it a public dep
# still resolves anonymously.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=gh_token,required=false \
    sh -euc 'GH="$(cat /run/secrets/gh_token 2>/dev/null || true)"; \
      [ -n "$GH" ] && export GIT_CONFIG_COUNT=1 \
        GIT_CONFIG_KEY_0="url.https://x-access-token:${GH}@github.com/.insteadOf" \
        GIT_CONFIG_VALUE_0="https://github.com/"; \
      uv sync --frozen --no-install-project --extra engine'

# Then the source, and install the project itself.
COPY README.md ./README.md
COPY src ./src
COPY libs ./libs
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts
# --no-editable: hatchling's editable hook only emits a .pth for one inferred
# root (src/), silently dropping libs/webhook_engine (the wheel target's
# multi-root `packages` list is only honoured on a real build). A real image
# shouldn't be an editable install anyway.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=gh_token,required=false \
    sh -euc 'GH="$(cat /run/secrets/gh_token 2>/dev/null || true)"; \
      [ -n "$GH" ] && export GIT_CONFIG_COUNT=1 \
        GIT_CONFIG_KEY_0="url.https://x-access-token:${GH}@github.com/.insteadOf" \
        GIT_CONFIG_VALUE_0="https://github.com/"; \
      uv sync --frozen --no-editable --extra engine'


FROM python:3.12-slim-bookworm AS runtime

# Non-root user; least privilege.
RUN groupadd --system lzt && useradd --system --gid lzt --home /app lzt

WORKDIR /app
COPY --from=builder --chown=lzt:lzt /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LZT_HEALTH_HOST=0.0.0.0 \
    LZT_HEALTH_PORT=27543

USER lzt
EXPOSE 27543

# Liveness for orchestrators that honour HEALTHCHECK (compose overrides this too).
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "scripts/health.py", "--retries", "1"]

ENTRYPOINT ["python", "-m", "lzt_eventus"]
CMD ["run"]
