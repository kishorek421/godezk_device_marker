# syntax=docker/dockerfile:1

# ── Base image ────────────────────────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────
COPY . .

# Collect static assets at build time. DJANGO_SECRET_KEY is a throwaway
# build-time value only — the real key comes from the env at runtime.
RUN DJANGO_SECRET_KEY=build-only DJANGO_DEBUG=0 python manage.py collectstatic --noinput

# Writable runtime dirs (MEDIA_ROOT, model cache mounted by k8s) and a
# non-root user to run the service.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p private_uploads models \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8008

# Liveness probe against the Django /health endpoint (stdlib only —
# slim images ship neither curl nor wget).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8008/health', timeout=4)" || exit 1

# Apply migrations, then serve via gunicorn. Port matches containerPort
# in k8s/services/device_marker.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:8008 --workers 3 --timeout 120"]
