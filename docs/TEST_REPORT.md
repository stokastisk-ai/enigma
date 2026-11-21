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
