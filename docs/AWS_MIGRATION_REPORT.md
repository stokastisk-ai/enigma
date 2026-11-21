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


## Update 2026-11-21 (cost and deployment review)
- Commit `4916f44` verified identical on the private `stokastisk-ai/enigma` remote.
- Added: itemized low/moderate/high cost model (`scripts/aws_cost_model.py`, tested), Lambda Web Adapter vs Lambda Web Functions comparison, Track A vs Track B decision record with the DynamoDB parity gate, pinned-JWKS mode (NAT-free VPC Lambda), `ENIGMA_PUBLIC_HOST`/`ENIGMA_PUBLIC_ORIGIN` fixes for CloudFront → Function URL.
- Expected monthly (us-east-1, list): Track A about USD 14.2 / 15.5 / 19.5 (low / moderate / high); Track B (not built) about USD 0.4 / 2.4 / 7.4. With always-free allowances (unverified for this PAID account): A about 14.0, B about 0.0–0.1.
- Still BLOCKED: credit expiry date (not in any API; inferred 2027-01-18 to 2027-07-18), your approval of an itemized plan, a non-root deployment identity (root access keys exist), measured Lambda timings, real Lambda Web Adapter behaviour, and Track B tooling approval/build. AWS deployed frontend/API/Cognito/DB: NOT RUN. Kaggle: NOT RUN.


## Update 2026-11-21 (Kaggle-only policy, Track B build)
| Item | Status | Evidence |
|---|---|---|
| Local tests | DONE | 317 passed / 6 skipped; smoke OK; SQL 61+78; lint/typecheck clean; 15 unit; 10 browser |
| Local GPU isolation | DONE | libraries and `nvidia-smi` blocked; env flag; network guard; nothing touched the 940M |
| No AWS GPU / hosted-AI path | DONE (audited, test-enforced) | `tests/test_no_aws_gpu.py` |
| DynamoDB Track B adapter | DONE locally (emulator) | 70+ parity tests on moto; **not run on real DynamoDB** |
| PostgreSQL Track A | DONE, untouched | 33 store tests + 61 SQL checks |
| Cognito JWKS rotation | DONE locally | cached JWKS, throttled refresh, retirement, fail-closed; not run against a real pool |
| CloudFront/Function-URL host handling | DONE locally, one bypass found and fixed | live local check |
| Idle claim back-off | DONE, default OFF | simulation; saves ≈ USD 0.03–0.37/month |
| Kaggle notebook | UPDATED, NOT RUN | measured assertions, offline/reconnect, secret-free evidence record |
| AWS account / credits | read-only, partial | PAID, USD 99.99, expiry unknown (console only); only root credentials exist; root access keys present |
| Root-key remediation | PLAN ONLY | `docs/AWS_ROOT_KEY_REMEDIATION.md` |
| AWS deployed frontend / API / Cognito / DB | **NOT RUN** | needs your itemized approval + non-root identity |
| Amplify with Next 16 | BLOCKED | AWS documents Next 12–15 only |
| Kaggle 2×T4 acceptance, browser → AWS → Kaggle | **NOT RUN** | needs a deployed API and your launching the notebook |
AWS resources created: **none**.


## Update 2026-11-21 (dashboards, parity hardening, Lambda packaging)
| Item | Status | Evidence |
|---|---|---|
| Git | 56c0739 verified private and in sync at start; new commit pushed at end | see final report |
| Local tests | DONE | 422 passed / 6 skipped; smoke OK; SQL 61+78; lint/typecheck clean; 15 unit; 16 browser; 0 vulnerabilities |
| AWS caller | **ROOT only** (no non-root profile; `vllm-temp` is empty) | cloud operations stopped; only local work and research |
| Root-key remediation | PLAN ONLY | `docs/AWS_ROOT_KEY_REMEDIATION.md`; deployer policy with explicit GPU denies drafted |
| Credit expiry / free-tier applicability | UNKNOWN | Billing console only; no API |
| CloudWatch dashboard | **DRAFT, not deployed** | 30 metrics, 15 offline validation tests, read-only pre-flight script |
| Enigma admin AWS page | **MOCK/SYNTHETIC built and tested; LIVE not run** | 26 backend + 6 browser tests |
| DynamoDB moto parity | DONE (strengthened) | 78+ tests; one real bug found and fixed |
| DynamoDB real-table parity | **NOT RUN** | guarded harness + throwaway template + measured cost; needs approval |
| Next.js 16 Lambda package | DONE locally | 22.8 MB, zero native binaries, architecture independent; CloudFront-style proxy checks 25/25 |
| Real Lambda + CloudFront + Web Adapter | **NOT RUN** | needs deployment approval |
| Cognito | verifier hardened and attack-tested locally; **real pool NOT RUN** | 50 tests |
| Idle polling | validated by simulation; default unchanged | saves under $0.4/mo |
| CloudFront vs raw Function URL | decided on numbers: start raw | list price: raw cheaper at every load |
| Kaggle build-5 on 2× T4 | **NOT RUN** | notebook ready (owner runs) |
| Browser → AWS → Kaggle E2E | **NOT RUN** | needs a deployed API |
AWS resources created or changed this session: **none**. Actual AWS spend: **not checked** (billing not accessible without root); do not read this as "$0".
