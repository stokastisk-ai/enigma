# Test report (refreshed 2026-11-21)

Host: Linux (Ubuntu 24), python3.13 3.13.15 (manual install; `python3.13 -m pip` only; no uv/ruff/venv/conda/Go), Node 24.22.2, npm 11.19.1, Google Chrome, PostgreSQL 18.6 binaries (throwaway local cluster). All counts below are from fresh runs after a clean `npm ci`.

## Local results
| Check | Result |
|---|---|
| `python3.13 -m pytest` | **114 passed, 5 skipped** (the 5 are live-Clerk tests, opt-in) |
| &nbsp;&nbsp;api / connector / SQL wrapper / route guards | 64 / 47 / 1 / 2 (+ the 5 live) |
| SQL migrations + RLS + job functions | **78 checks pass**; hosted verifier `supabase/verify/rls_two_tenant.sql` **25 checks pass** and leaves no rows/objects (proved) |
| `python3.13 scripts/smoke_e2e.py` | SMOKE OK (all synthetic) |
| `npm ci` -> `tsc --noEmit` -> `eslint` -> `npm run build` | all clean (ESLint 0 findings) |
| `npm run test:e2e` (mock mode, Chrome) | **10 passed**: onboarding, one-time key, streaming chat, Stop, per-GPU pages labelled Synthetic, offline/reconnect, key rotation, 2-user isolation, BFF allow-list, WCAG A/AA (dark+light), mobile |
| `npm audit --omit=dev` | 0 vulnerabilities |

**111 vs 112:** the README's 111 was the count before one more connector test (auto spool-dir cleanup) was added; 112 was correct afterwards. Today's default total is 114 (two new route-guard tests) plus 5 opt-in live tests.

## Live results against a real Clerk DEVELOPMENT instance (free)
`ENIGMA_LIVE_CLERK=1 python3.13 -m pytest tests/live` -> **5 passed**: real Clerk-signed RS256 session JWTs verified through Clerk's real JWKS endpoint; corrupted/swapped/forged/HS256 tokens rejected with 401; two real users isolated (404 on every cross-tenant route).
`npm run test:e2e:live` -> **1 passed**: real sign-up with test email + verification code, onboarding, BFF using a real browser session token (azp enforced), second user isolated, anonymous browser refused, sign-out locks the BFF and `/app`, sign-in restores the same workspace. Throwaway users were deleted (0 remain).
These are development-instance results only; they say nothing about a production Clerk instance.

## Disk (measured after cleanup, 2026-11-21)
Project directory **718 MiB**: `apps/web/node_modules` 714 MiB (regenerable with `npm ci`), `.git` 1.9 MiB, source ~3 MiB. Generated `.next` (~114 MiB) and `.next-e2e` (~107 MiB) were removed after the runs and are rebuilt by the build commands. The earlier 574.4 MiB screenshot predates Playwright/axe and the final builds; 714 MiB for `node_modules` was reproduced after a clean `npm ci`. **Tracked source on GitHub: 157 files, 1.19 MB** (largest: the 357 KB lockfile).

## Secret audit
Built-in regex scan of the working tree and every Git object (Clerk sk/pk, Google AIza, JWT-shaped, Postgres URLs with passwords, private keys, AWS/GitHub tokens, Supabase keys, runtime tokens): **no real credential found**; history had 0 commits. Four generic hits were hard-coded throwaway dev-login strings in test tooling, now generated randomly per run. No scanner such as gitleaks is installed; this was a pattern scan, not a guarantee.

## Status table
| Item | Status |
|---|---|
| Local app + tests | **DONE** |
| Private GitHub push (`stokastisk-ai/enigma`, branch `main`) | **DONE** - checkpoint commit `e60d11d697d01c8a556bfc50012304736a5b6a45`; remote SHA equals local; repo verified PRIVATE (API + anonymous 404); 157 files, no forbidden paths, no secrets (index + literal-value scan) |
| Real Clerk dev instance (JWKS, sign-up/in/out, isolation) | **DONE** (dev only) |
| Supabase live (project, RLS, private Realtime, Clerk->Supabase JWT) | **BLOCKED** - no credentials/CLI; verifier + steps ready |
| Vercel preview | **BLOCKED** - Hobby: non-commercial only + no private org repos (see BLOCKED_PRIVATE_ORG_HOBBY.md) |
| Kaggle 2xT4 build-5 acceptance | **NOT RUN** - needs a reachable authorized endpoint; local-only today |
| Clerk production instance | **NOT RUN** - needs domain + approval |

## Still true from before
Not verified: real vLLM `/metrics` values, real T4 NVML, TP=2/PP=2, other models, behaviour under real load. NVML was sampled only on this host's GeForce 940M (shape/plausibility). `SupabaseStore` is tested only against a mocked transport; the SQL itself on local Postgres with a Supabase shim.


---
## AWS migration run (2026-11-21) — executed in this session
| Command | Result |
|---|---|
| `python3.13 -m pytest` | 167 passed, 5 skipped (live Clerk, opt-in) — baseline before changes: 114 + 5 |
| `python3.13 scripts/smoke_e2e.py` | SMOKE OK (synthetic data) |
| `cd apps/web && npm run lint && npm run typecheck && npm run build` | clean |
| `npm run test:e2e` | 10 passed (mock mode) |
| `npm run test:unit` | 15 passed (Cognito claims/IDP/CSRF; local keys) |
| `python3.13 tests/sql/run_sql_tests.py` | 78 checks passed (historical Supabase shim) |
| `python3.13 tests/sql/run_aws_sql_tests.py` | 61 checks passed (portable schema, PG18 throwaway cluster) |
| `tests/api/test_postgres_store.py` | 33 passed (27 re-run API tests + 6 Postgres-specific) |
| Manual curl run vs fake Cognito + `next dev` | redirects/401/403/cookie flags/sign-out as designed |
All Cognito/Postgres results above use **local test doubles/clusters**, not AWS. Live AWS, Kaggle GPU and Supabase remain unverified.


---
## Re-run with the exact interpreter and local-GPU guard (2026-11-21, later session)
Interpreter: `/usr/local/bin/python3.13` (resolves to `/usr/local/python3.13/bin/python3.13`, 3.13.15). `pip check`: no broken requirements. All GPU libraries blocked and `ENIGMA_FORBID_LOCAL_GPU=1` for every run below.
| Command | Result |
|---|---|
| `/usr/local/bin/python3.13 -m pytest` | **176 passed, 6 skipped** (5 opt-in live Clerk + 1 remote-only real-NVML). Earlier README claim: 167 passed / 5 skipped |
| `scripts/smoke_e2e.py` | SMOKE OK (synthetic) |
| `tests/sql/run_aws_sql_tests.py` / `run_sql_tests.py` | 61 / 78 checks passed (+25 rollback-only verifier) |
| `npm run lint`, `typecheck`, `build`, `test:unit` (15), `npm audit --omit=dev` | clean; 0 vulnerabilities |
| `npm run test:e2e` (Chrome) | 10 passed |
| Standalone Next 16 bundle smoke (Node, no AWS) | `/`,`/sign-in` 200; `/app` unauth 307; BFF 401; cross-origin POST 403 |
**Disclosure:** the earlier AWS-session runs executed `test_real_nvml_on_this_host_if_present`, which initialised NVML and made read-only queries against the laptop GPU (two full pytest runs). It has been replaced by a remote-only test and the guard above. No GPU state was changed; no other GPU path was executed.
Not run: real AWS, real Cognito/RDS, Kaggle T4, browser→AWS→Kaggle.


---
## Cost-model / deployment-review run (2026-11-21, after commit 4916f44)
Interpreter `/usr/local/bin/python3.13`; local GPU guard active (`ENIGMA_FORBID_LOCAL_GPU=1`, GPU libraries blocked); no AWS resource created or modified (read-only CLI and Price List API only).
| Command | Result |
|---|---|
| `/usr/local/bin/python3.13 -m pytest` | **184 passed, 6 skipped** (5 opt-in live Clerk + 1 remote-only GPU). +8 vs 176: 3 pinned-JWKS tests, 5 cost-model tests |
| `scripts/smoke_e2e.py` | SMOKE OK (synthetic) |
| `tests/sql/run_aws_sql_tests.py` / `run_sql_tests.py` | 61 / 78 checks passed |
| `npm run lint`, `typecheck`, `test:unit` (15, incl. new public-host CSRF cases), `test:e2e` | clean; 15 pass; 10 passed |
Evidence limits: pinned-JWKS and CSRF/public-host logic are proven with local keys and unit cases only. Behaviour behind real CloudFront/Lambda Function URL/Web Adapter, cookie pass-through and cold-start latency are **NOT RUN**. Cost figures are model outputs from list prices, not billing data.


---
## Kaggle-only / Track B run (2026-11-21, after commit fd3f759)
Interpreter `/usr/local/bin/python3.13`. Local GPU never touched: GPU libraries and `nvidia-smi` blocked, `ENIGMA_FORBID_LOCAL_GPU=1`, and a session-wide guard refuses every non-loopback socket (so no test can reach AWS). No AWS resource created or modified; AWS API calls this session were limited to the caller-identity check.
| Command | Result |
|---|---|
| `/usr/local/bin/python3.13 -m pytest` | **317 passed, 6 skipped** (5 opt-in live Clerk + 1 remote-only GPU). Previous reported: 184 / 6 |
| `scripts/smoke_e2e.py` | SMOKE OK (synthetic) |
| `tests/sql/run_aws_sql_tests.py` / `run_sql_tests.py` | 61 / 78 checks passed |
| `npm run lint`, `typecheck`, `test:unit` (15), `npm audit --omit=dev`, `test:e2e` | clean; 15 pass; 0 vulnerabilities; 10 passed |
New coverage: `tests/api/test_dynamodb_store.py` (DynamoDB parity gate on **moto**, incl. the whole existing API suite, 8-thread claim race, concurrent ordered events, caps under a race, idempotent submit, failure injection, retention, durability, isolation, row-shape parity, no-GSI/consistent-read assertions); `tests/test_no_aws_gpu.py` (no AWS GPU/hosted-AI/CUDA-in-Lambda anywhere, with self-tests of the detectors); `tests/test_network_guard.py`; Cognito JWKS rotation tests (+9); claim-poll pacing tests (simulated clock) in `tests/connector/test_pacing.py`.
Manual live check (real Next server + fake Cognito IDP + curl, no AWS): CloudFront-style Origin via a Function-URL `Host` accepted; evil Origin 403; a spoofed `X-Forwarded-Host` bypass was found, fixed (header no longer trusted) and re-verified; sign-in redirect stays on `ENIGMA_PUBLIC_ORIGIN`.
**Evidence limits (NOT RUN):** real DynamoDB/RDS/Cognito/Lambda/CloudFront/Amplify; Lambda Web Adapter for Next 16; Kaggle 2×T4 acceptance (notebook updated, to be run by you); browser → AWS → Kaggle end-to-end. moto is an emulator: no real throttling, partition or transaction-timing behaviour. Pacing latency figures are from a deterministic simulation, not measured network latency.


---
## Dashboard / admin page / Track B hardening run (2026-11-21, after commit 56c0739)
Interpreter `/usr/local/bin/python3.13`. Local GPU never touched (GPU libraries and `nvidia-smi` blocked, `ENIGMA_FORBID_LOCAL_GPU=1`, loopback-only network guard). **No AWS call was made and no AWS resource was created or changed**; AWS access is root-only, so cloud operations were not attempted. Results below are fresh executions on the final tree.
| Command | Result |
|---|---|
| `/usr/local/bin/python3.13 -m pytest` | **422 passed, 6 skipped** (5 opt-in live Clerk + 1 remote-only GPU; the same six as before). Previous reported: 317 / 6 |
| `scripts/smoke_e2e.py` | SMOKE OK (synthetic) |
| `tests/sql/run_aws_sql_tests.py` / `run_sql_tests.py` | 61 / 78 checks passed |
| `npm run lint`, `typecheck`, `test:unit` (15), `npm audit --omit=dev` | clean; 15 pass; 0 vulnerabilities |
| `npm run test:e2e` (Chrome) | **16 passed** (10 existing + 6 new `aws-admin`) |
| `scripts/validate_nextjs_lambda.py --build` | 25 PASS, 0 FAIL, 2 INFO (package, CloudFront-style proxy behaviour); not the real Lambda Web Adapter |
New coverage this run: `tests/api/test_awsops.py` (26: admin authorization, honesty states, read-only/bounded AWS access against moto, cache, scrubbing, datapoint round-trip) · `tests/test_cloudwatch_dashboard.py` (15) · `tests/test_iac_governance.py` (10) · `tests/test_live_ddb_guard.py` (20) · +13 DynamoDB parity tests (pagination, Unprocessed*, conflicts, throttling, runtime cap, expiry/cancel, TTL laziness, truncation) · +9 Cognito attack tests · +1 cost-model test · `e2e/aws-admin.spec.ts`.
**Defects found by the new tests and fixed:** `DynamoStore._batch_get` silently returned a partial list after repeated `UnprocessedKeys` (now retries with backoff and raises); an **empty `sub`** in a validly signed token was accepted (now rejected, subject validated); middleware 401/redirect lacked `Cache-Control: no-store`; shared chart wrapper nested an interactive element inside `role="img"` (axe `nested-interactive`); admin page showed "ago ago", an uptime as an age, a misleading linear topology, and "present" for synthetic rows; admin rate limit (burst 10) was tight enough to throttle normal navigation (now 30). Two earlier test failures were test bugs, not product bugs (a `/me` contract assertion updated for the new `is_admin` field; a secret-fragment assertion that broke whenever the random secret contained `_`; the full secret was verified never stored in 40 trials).
**NOT RUN (evidence limits):** real DynamoDB (the guarded harness exists), real Lambda/CloudFront/Cognito, the Lambda Web Adapter itself, the CloudWatch dashboard in AWS, the admin page in `live` mode, Kaggle 2× T4 acceptance, browser → AWS → Kaggle end-to-end. moto is an emulator. Simulated polling latency is not measured network latency.

## 2026-11-22 Amplify/Next.js 16 session (fresh runs, CPU-only, no AWS writes, local GPU untouched)
| Check | Result |
|---|---|
| `/usr/local/bin/python3.13 -m pytest` (428 collected) | exit 0; 422 passed, 6 skipped (same six) |
| `scripts/smoke_e2e.py`; `tests/sql/run_aws_sql_tests.py` | SMOKE OK; SQL suite ran clean |
| `npm run typecheck`, `npm run lint`, `npm run test:unit` | clean; clean; 15/15 |
| `npm run build` (Next 16.4.6) | OK, 16 routes all dynamic |
| `npm run test:e2e` (mock) | 16 passed |
| `scripts/validate_nextjs_lambda.py --build` | 0 failures (22.8 MB, 0 native binaries) |
| `scripts/build_amplify_bundle.py --serve-check` | 20/20 bundle checks pass (second pass); NOT proof of Amplify support |
Verdict and sources: `docs/AWS_AMPLIFY_NEXT16.md`. Real Amplify/Lambda/Cognito/DynamoDB, Kaggle 2xT4 and browser→AWS→Kaggle E2E: NOT RUN.

## 2026-11-22 audit pass (second session; CPU-only, no AWS writes, local GPU untouched)
| Check | Result |
|---|---|
| `/usr/local/bin/python3.13 -m pytest` | **429 passed, 6 skipped** (same six); +7 vs previous: Amplify governance (6, incl. hostile env values under `/bin/sh` and bash) and Amplify cost line (1) |
| typecheck / lint / node unit | clean / clean (after ignoring generated `.amplify-hosting/**`; before that fix lint scanned the bundle: 16,180 problems) / 15 of 15 |
| Mock Playwright E2E | 16 passed |
| `validate_nextjs_lambda.py` | 0 failures |
| `build_amplify_bundle.py --build --serve-check` | 20/20 |
Evidence limits unchanged: real Amplify, LWA, CloudFront, Cognito, DynamoDB, Kaggle 2×T4 and browser→AWS→Kaggle are NOT RUN.

## 2026-11-22 (session 3): connector contract fix, teardown script, web-tier IaC draft, notebook governance
- pytest: 448 passed, 6 skipped (same 5 live-Clerk + 1 remote-only GPU skips); new tests: `test_aws_teardown.py` (9, fake aws runner, no AWS call), `test_kaggle_notebook.py` (5, static), `test_iac_governance.py` +3 (web template), `test_executor.py` +1 (chunk_count, never token_count).
- NOT RUN: real AWS (no resource exists), Kaggle GPU (build 5 / TP=1 / TP=2), browser→AWS→Kaggle path. See `docs/E2E_DATA_FLOW.md`.
