# Build the application separately so the runtime contains no Node toolchain.
FROM node:24-alpine AS application-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# CodeAtlas API, worker, and web application image.
# Built and pushed by .github/workflows/cd.yml; built and smoke-tested on every PR by ci.yml.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Set by the workflow: the commit SHA this image was built from (shown on /health).
ARG GIT_SHA=unknown

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Dependencies first so this layer stays cached across code changes. Dense model
# dependencies are excluded; the worker reports their unavailability and uses the
# configured semantic fallback rather than producing dense-model results.
COPY pyproject.toml uv.lock README.md ./
COPY scripts/docker_excludes.py ./scripts/
RUN uv sync --locked --no-dev --no-install-project $(python scripts/docker_excludes.py uv.lock)

COPY src ./src
COPY --from=application-build /frontend/dist ./src/codeatlas/web/static/application
COPY migrations ./migrations
COPY alembic.ini ./
COPY tests/fixtures ./tests/fixtures
RUN uv sync --locked --no-dev $(python scripts/docker_excludes.py uv.lock)

# Seed the default store with the bundled sandbox evaluations. A mounted volume
# can replace this directory; an existing volume must be seeded separately.
COPY deploy/bundles /data/bundles

# Run as a non-root user; the data directory remains writable for new bundles.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin codeatlas
RUN mkdir -p /data/bundles /data/repo-cache && chown -R codeatlas:codeatlas /data

ENV PATH="/app/.venv/bin:$PATH" \
    CODEATLAS_BUNDLE_DIR=/data/bundles \
    CODEATLAS_REPO_CACHE_DIR=/data/repo-cache \
    CODEATLAS_COMMIT=$GIT_SHA \
    PORT=8000

LABEL org.opencontainers.image.title="codeatlas" \
      org.opencontainers.image.description="CodeAtlas: Jira-to-GitHub traceability web service" \
      org.opencontainers.image.source="https://github.com/codeatlas-fyp/codeatlas" \
      org.opencontainers.image.revision=$GIT_SHA

USER codeatlas
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}/health\", timeout=4)"]

# Hosts such as Render pass the port in $PORT, so the shell form expands it.
CMD ["sh", "-c", "exec uvicorn codeatlas.api.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
