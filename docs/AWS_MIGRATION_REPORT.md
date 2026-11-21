# AWS migration report — 2026-11-21

## Final status
| Item | Status | Evidence |
|---|---|---|
| LOCAL TESTS | **DONE** | pytest **176 passed, 6 skipped** after the GPU-isolation work (earlier: 167 / 5); smoke OK; 10/10 Playwright mock tests; lint, typecheck, `next build` clean; 15 Node unit tests; SQL 78 (Supabase) + 61 (portable) checks on throwaway PG18 |
| AWS account plan | **DONE (read-only)** | `freetier get-account-plan-state`: PAID, ACTIVE, USD 99.99 credits. Credit expiry **BLOCKED** (console only) |
| AWS cost check | **BLOCKED** | `AWS_COST_MATRIX.md`; owner approvals required; no free-forever claim possible |
| AWS code migration | **DONE locally, flag-gated** | Cognito verifier + web auth, `PostgresStore`, portable SQL, Lambda handler. Clerk/Supabase code retained |
| AWS deployed frontend | **BLOCKED** | Amplify documents Next.js 12–15 only; 16.4.6 in use |
| AWS deployed API | **NOT RUN** | `infra/aws/template.yaml` validated read-only only |
| Auth | **PARTIAL** | claim handling + cookie/CSRF flow tested against **local test doubles**; no real Cognito pool |
| DB isolation | **PARTIAL** | proven on local PG18 (tenant 404s, privileges, SKIP LOCKED race); not RDS |
| Kaggle GPU | **NOT RUN** | user-initiated only |
| End-to-end realtime | **NOT RUN** | needs deployed API + Kaggle |

## What was built (files)
`apps/api/enigma_api/{security,settings,main}.py` (Cognito + postgres flags) · `store/postgres.py` · `apps/api/lambda_handler.py`, `requirements-aws.txt` · `infra/aws/sql/0001,0002` · `infra/aws/{template.yaml,amplify.yml}` (drafts) · `apps/web/lib/server/{cognito,env,session}.ts`, `proxy.ts`, `app/api/auth/[action]`, `components/enigma/cognito-auth.tsx`, sign-in/up pages · tests: `test_cognito_auth.py`, `test_postgres_store.py`, `test_lambda_handler.py`, `tests/sql/test_aws_jobs.sql`, `cognito.test.ts` · docs `AWS_*`.

## Findings worth knowing
- Supabase RLS/Realtime were only needed for browser-direct access, which Enigma never uses; dropping them loses no isolation (checks moved to app + SQL-function ids, tested).
- Retry-safety: `PostgresStore` never replays a sent statement (avoids duplicate inserts); idle connections are probed.
- A mistaken test setup first sent a fake sign-in to the real Cognito endpoint (fake client id, fake email, no credentials involved) because the loopback test hook is disabled under `NODE_ENV=production` by design; rerun correctly under `next dev`.
- `pip install --user psycopg[binary] mangum` was run with your approval (psycopg 3.3.9, mangum 0.22.0).

## Not done / next
Rotate previously pasted credentials; confirm credit expiry; choose Track A/B and a NAT-free network design; test Next 16 on Amplify; real Cognito + RDS tests only after approval. No commit or push was made.
