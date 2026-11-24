# Deployment (Railway)

Enigma runs as three services in one Railway project/environment: `Enigma` (Next.js web), `enigma-api` (FastAPI) and `Postgres`. Both application services are built from this repository's `main` branch with the **repository root as the Docker build context** (the web app imports `packages/contracts`), use Railway Serverless, run one replica, and run as a non-root user.

| | web | api |
|---|---|---|
| Dockerfile | `apps/web/Dockerfile` | `apps/api/Dockerfile` |
| Port | `PORT=3000` | `PORT=8000` |
| Health check | `/` | `/api/v1/health` (liveness) |
| Readiness | – | `GET /api/v1/admin/ready` with `Authorization: Bearer $CRON_SECRET` (checks the database) |
| Watch paths | `apps/web/**`, `packages/contracts/**` | `apps/api/**`, `infra/aws/sql/**` |
| Pre-deploy | – | `python -m enigma_api.bootstrap` (applies the portable schema and creates the least-privilege database role when `ENIGMA_DB_ADMIN_DSN` is set; a no-op otherwise) |

Do not use `railway.toml` (Railway deprecated config-as-code) or `railway up .` from the repository root: use `scripts/railway_stage_upload.py` + `scripts/railway_enigma_deploy.py` for an exact upload, or let the GitHub-connected services build `main`.

## Variables (names only; values live in Railway, never in Git)
- **web:** `PORT`, `ENIGMA_AUTH_PROVIDER=cognito`, `NEXT_PUBLIC_COGNITO_REGION`, `NEXT_PUBLIC_COGNITO_USER_POOL_ID`, `NEXT_PUBLIC_COGNITO_CLIENT_ID`, `ENIGMA_PUBLIC_ORIGIN`, `ENIGMA_PUBLIC_HOST`, `ENIGMA_API_URL` (private `http://<api>.railway.internal:<port>`).
- **api:** `PORT`, `ENIGMA_STORE=postgres`, `ENIGMA_DB_DSN` (least-privilege role, `sslmode=require`), `ENIGMA_DB_APP_PASSWORD`, `ENIGMA_AUTH_PROVIDER=cognito`, `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, `CRON_SECRET`, `ENIGMA_AWS_OPS=off`. `ENIGMA_DB_ADMIN_DSN` exists only during the first bootstrap and is then deleted.
- Never set `ENIGMA_DEV_AUTH` in production: both tiers refuse it on Railway.

## Authentication
Amazon Cognito user pool with one public app client (no secret; `USER_PASSWORD_AUTH` + refresh tokens; email as username; email confirmation code; no hosted UI, so no callback URLs). The BFF holds tokens in `HttpOnly; Secure; SameSite=Lax` cookies (access: `Path=/`, refresh: `Path=/api`) and the API independently verifies the access token (issuer, signature via the pool JWKS, `token_use=access`, client binding) and enforces tenant membership.

## Kaggle connector
Set `ENIGMA_API_URL` to the API's public HTTPS origin and `ENIGMA_RUNTIME_TOKEN` (minted once in the UI, stored in a Kaggle secret). The connector polls outbound only; while it runs it keeps the API awake, and when it stops the runtime turns offline after its heartbeat expires and the API can sleep.

## Retention
`.github/workflows/retention.yml` calls `GET /api/v1/admin/prune` daily. It needs the repository secret `ENIGMA_CRON_SECRET` and the repository variable `ENIGMA_API_URL`.

## Cost
Railway Hobby ($5/month, including $5 of usage; not a spending cap). Set a workspace usage limit (soft $5, minimum hard $10) and check `railway usage` after changes. Postgres is the only always-on service.
