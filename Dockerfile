# syntax=docker/dockerfile:1
# Lightweight image for a 512 MB Render free web service.
FROM python:3.10-slim

# Prevent Python from writing .pyc files and buffering stdout.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TEMP_DIR=/tmp/file-renamer

WORKDIR /app

# System deps: TgCrypto is a manylinux wheel, no compiler needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user for safety.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /tmp/file-renamer \
    && chown -R appuser:appuser /app /tmp/file-renamer
USER appuser

EXPOSE 8080

# Render provides a health check against /health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/health || exit 1

CMD ["python", "main.py"]
