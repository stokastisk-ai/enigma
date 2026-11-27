# Test report

Reproduce locally (CPU only; the laptop GPU is never touched): `python3.13 -m pytest`, then in `apps/web`: `npm ci && npm run typecheck && npm run lint && npm run test:unit && npm run test:e2e && npm audit --omit=dev`.

Current counts and the date of the last run are recorded in `docs/ENIGMA_FINAL_RAILWAY_PRODUCTION_AUDIT.md` (kept next to the production audit so they cannot go stale in two places).

## What each suite proves
| Suite | Proves | Does not prove |
|---|---|---|
| `tests/api` | auth positive/negative, tenant isolation, job lease/claim/idempotency/ordering, rate and body limits, telemetry validation, readiness, Cognito token verification (local keys), DB bootstrap | behaviour of a real identity provider (covered by the live Railway E2E) |
| `tests/api/test_postgres_store.py`, `tests/test_sql_suite.py` | the portable schema and `PostgresStore` against a real throwaway PostgreSQL | Railway's PostgreSQL itself (checked separately by readiness + the live E2E) |
| `tests/api/test_dynamodb_store.py` | `DynamoStore` parity on a moto emulator | real-AWS DynamoDB (opt-in, `docs/AWS_DYNAMODB_REAL_PARITY.md`) |
| `tests/connector` | connector pacing, retries, spool, GPU/vLLM metric parsing with fakes | a real GPU (Kaggle only; the single real-GPU test is skipped locally) |
| `tests/test_railway_*` | Dockerfile/requirements governance, hosting guards, exact-upload stager and launcher guards | a live deployment |
| `tests/test_no_aws_gpu.py`, `tests/test_iac_governance.py` | no GPU/hosted-AI path anywhere; remaining AWS policies and DynamoDB templates are least-privilege | AWS behaviour |
| `apps/web` unit / Playwright | proxy, page guards, public paths, cookies, hosted-env guard; mock-mode console flows with axe checks | real sign-in (live E2E) |

## Skips that are expected
- `tests/connector/test_metrics_and_gpu.py`: REMOTE-ONLY (touches a physical GPU; Kaggle host only).
- `tests/live/test_clerk_live.py` (5): need `ENIGMA_LIVE_CLERK=1` and a Clerk dev instance.
- `tests/connector/test_sdk_integration.py`: needs the Stokastisk SDK source (`ENIGMA_SDK_SRC`).

Mock data is always labelled Synthetic; mock runs and mock telemetry are never counted as real GPU evidence.

## 2026-11-27 — OCI migration branch (local evidence; no deployment, no Kaggle GPU run)
| Suite | Result |
|---|---|
| `python3.13 -m pytest -q` (API, SQL on local PostgreSQL 18, connector, notebooks) | 778 passed, 5 skipped (GPU-only / evidence-dir-only) |
| `apps/web`: `npm run test:unit`, `npx next build` | 43/43, build OK (Next.js 16.4.6) |
| `scripts/local_auth_e2e.py` (PostgreSQL 17, SMTP sink, next start, uvicorn) | 24/24 (sign-up/code/sign-in, two tenants, CSRF, sign-out, role separation) |
| `scripts/oci_import.py --dry-run` on the real Neon backup (PostgreSQL 18.7) | 20/20 checks; schema identical to a fresh bootstrap |
| `deploy/oci` compose smoke on x86 (local CA, not public TLS) | edge routing, private routes 404, sign-up closed, diag ingest on PostgreSQL, restart persistence, restore drill |
Not run: ARM64 build (A1 capacity), public TLS, live OCI acceptance, Kaggle notebooks 01-03 on GPU.
