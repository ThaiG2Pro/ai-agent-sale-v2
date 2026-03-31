FROM python:3.13-slim-bookworm AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --compile-bytecode
# Trim ingestion-only heavy packages from API runtime image.
# Use `uv pip` against the venv because pip module/binary is not present by default.
RUN uv pip uninstall --python /opt/venv/bin/python \
    crawl4ai \
    patchright \
    playwright \
    tf-playwright-stealth \
    pymupdf \
    scipy || true


FROM python:3.13-slim-bookworm AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libssl3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 appuser

COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appuser . /app

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/readiness', timeout=5)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--loop", "uvloop", "--http", "httptools"]
