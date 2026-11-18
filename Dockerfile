# syntax=docker/dockerfile:1

# ── Base image ────────────────────────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────
# Core requirements only — the optional ML stack (requirements-agent.txt)
# is intentionally excluded to keep the web/worker image small.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────
COPY . .

# Collect static assets at build time (WhiteNoise serves them at runtime).
# DJANGO_SECRET_KEY is a throwaway build-time value — the real key comes
# from the environment at runtime.
RUN DJANGO_SECRET_KEY=build-only DJANGO_DEBUG=0 python manage.py collectstatic --noinput

# Writable runtime dirs (MEDIA_ROOT mounted by k8s) and a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p private_uploads \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8008

# Liveness probe — stdlib only (slim images ship neither curl nor wget).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8008/health', timeout=4)" || exit 1

# Apply migrations, seed demo users on first boot (empty DB only), then
# serve via gunicorn. Port matches containerPort in k8s/services/device_marker.
# For the annotation job worker, deploy the same image with command
# override: python manage.py runworker
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py seed_demo --if-empty && exec gunicorn config.wsgi:application --bind 0.0.0.0:8008 --workers 3 --timeout 120"]
