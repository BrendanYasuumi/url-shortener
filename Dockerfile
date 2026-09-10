# syntax=docker/dockerfile:1

# Install Python dependencies separately so build tools and pip caches do not
# become part of the final runtime image.
FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -r requirements.txt


# The runtime stage contains only Python, the virtual environment, and app code.
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="URL Shortener API" \
      org.opencontainers.image.description="FastAPI URL shortener with SQLite analytics"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    URL_SHORTENER_DB_PATH=/data/urls.db

# Use a fixed unprivileged identity and prepare the persistent database path
# before switching away from root.
RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid appgroup --no-create-home \
        --shell /usr/sbin/nologin appuser \
    && mkdir -p /data \
    && chown appuser:appgroup /data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=appuser:appgroup app ./app

USER appuser

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
