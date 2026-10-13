# CodeAtlas web service image (read-only API + pages over stored evidence bundles).
# Built and pushed by .github/workflows/cd.yml; built and smoke-tested on every PR by ci.yml.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Set by the workflow: the commit SHA this image was built from (shown on /health).
ARG GIT_SHA=unknown

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Dependencies first so this layer stays cached across code changes. The excludes script
# leaves out sentence-transformers/torch/CUDA: the server never computes embeddings.
COPY pyproject.toml uv.lock README.md ./
COPY scripts/docker_excludes.py ./scripts/
RUN uv sync --locked --no-dev --no-install-project $(python scripts/docker_excludes.py uv.lock)

COPY src ./src
RUN uv sync --locked --no-dev $(python scripts/docker_excludes.py uv.lock)

# Demo data: real evaluations of the codeatlas-sandbox PRs, produced by `codeatlas collect`
# (see deploy/bundles/README.md). Other bundles can be mounted over /app/bundles.
COPY deploy/bundles ./bundles

# Run as a non-root user that cannot modify the code or the bundles.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin codeatlas

ENV PATH="/app/.venv/bin:$PATH" \
    CODEATLAS_BUNDLE_DIR=/app/bundles \
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
CMD ["sh", "-c", "exec uvicorn codeatlas.api.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
