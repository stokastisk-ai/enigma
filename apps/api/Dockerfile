# Enigma control-plane API on Railway. BUILD CONTEXT = REPOSITORY ROOT (Root Directory unset); set service variable
# RAILWAY_DOCKERFILE_PATH=apps/api/Dockerfile. CPU-only: no GPU libraries, no inference.
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENIGMA_ENV=production PORT=8000

WORKDIR /srv
COPY apps/api/requirements.txt apps/api/requirements-railway.txt ./
RUN pip install -r requirements-railway.txt
COPY apps/api/enigma_api ./enigma_api
# Portable schema for `python -m enigma_api.bootstrap` (a no-op unless ENIGMA_DB_ADMIN_DSN is set).
COPY infra/aws/sql ./sql

RUN useradd --system --uid 10001 --no-create-home enigma
EXPOSE 8000
USER 10001

# Railway injects $PORT. One worker: PostgresStore keeps one lock-guarded connection per process and the in-process rate limiter is per process.
# Do not use --forwarded-allow-ips='*': nothing in the API reads client IPs, and trusting arbitrary X-Forwarded-* is never needed here.
CMD ["sh", "-c", "exec python -m uvicorn enigma_api.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-server-header --no-access-log"]
