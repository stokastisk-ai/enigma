# Enigma control-plane API. BUILD CONTEXT = REPOSITORY ROOT. Multi-arch base: builds natively on the OCI A1 (linux/arm64) and on x86_64.
# CPU-only: no GPU libraries, no inference. API_REQUIREMENTS selects the runtime set (requirements-oci.txt: PostgreSQL only, no AWS SDK).
FROM python:3.13-slim-bookworm
ARG API_REQUIREMENTS=requirements-oci.txt

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENIGMA_ENV=production PORT=8000

WORKDIR /srv
COPY apps/api/requirements.txt apps/api/requirements-oci.txt apps/api/requirements-railway.txt ./
RUN pip install -r "${API_REQUIREMENTS}"
COPY apps/api/enigma_api ./enigma_api
# Portable schema for `python -m enigma_api.bootstrap` (a no-op unless ENIGMA_DB_ADMIN_DSN is set).
COPY db/migrations ./sql

RUN useradd --system --uid 10001 --no-create-home enigma
EXPOSE 8000
USER 10001

# One worker: PostgresStore keeps one lock-guarded connection per process and the in-process rate limiter is per process.
# Do not use --forwarded-allow-ips='*': nothing in the API reads client IPs, and trusting arbitrary X-Forwarded-* is never needed here.
CMD ["sh", "-c", "exec python -m uvicorn enigma_api.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-server-header --no-access-log"]
