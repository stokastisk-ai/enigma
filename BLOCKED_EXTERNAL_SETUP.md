# Status of external setup (updated 2026-11-21)

Legend: DONE = executed and verified in this repository's history; BLOCKED = needs something I do not have or may not do; NOT RUN = not attempted.

| Item | Status | Detail |
|---|---|---|
| Private GitHub repo `stokastisk-ai/enigma` | **DONE** | verified PRIVATE (authenticated API and anonymous 404); checkpoint commit `e60d11d` pushed to `main`, no force |
| Clerk free **development** instance (sign-up, sign-in, sign-out, real JWKS, 2-user isolation) | **DONE** (dev instance only) | dedicated app "Enigma (dev)" created; keys in ignored `apps/web/.env.local`; 5 API + 1 browser live tests pass |
| Clerk **production** instance / domains | BLOCKED | needs a real domain and explicit decision; `pk_test_` keys are never for production |
| Clerk -> Supabase third-party JWT | BLOCKED | needs the Supabase project |
| Supabase project, migrations, RLS, private Realtime | **BLOCKED** | no Supabase token/CLI/credentials available here. Steps below; verifier script is ready |
| Vercel deployment | **BLOCKED** | see BLOCKED_PRIVATE_ORG_HOBBY.md |
| Kaggle 2xT4 build-5 acceptance | **NOT RUN** | needs a reachable authorized Enigma endpoint first (local-only today); you run the notebook |

## Supabase: exact steps (dashboard, free plan)
1. Sign in at supabase.com with the account you intend to use. Check the Free-plan allowance on the billing page (free projects per organization, 500 MB database, pausing after about a week of inactivity - figures from `skills.md`, **not re-verified today**).
2. **New project** named `enigma` (do not reuse any existing project and never run these migrations against `agentic_workspace` or any other database). Choose the Free plan; no payment method is needed. Save the database password in a password manager; never paste it into chat.
3. SQL editor: run `supabase/migrations/0001_core_schema.sql`, then `0002_job_functions.sql`. Review them first; they only create Enigma tables/functions in `public`.
4. SQL editor: paste `supabase/verify/rls_two_tenant.sql`. It runs 25 isolation/Realtime-policy checks inside a transaction that ends in `ROLLBACK`; success = no error and a NOTICE "all 25 checks passed".
5. Dashboard > Realtime settings: disable "Allow public access" so only private channels work.
6. Dashboard > Authentication > Sign In / Providers > Third-Party Auth > add **Clerk** and select the Enigma dev Clerk domain. In the Clerk dashboard enable the Supabase integration so session tokens carry `role: authenticated`.
7. Copy project URL and publishable key into `apps/web/.env.local` (names in `.env.example`); service-role key only into the API's server-side env. Rotate the key later if it is ever pasted anywhere.
8. Re-run: Clerk-signed-in user can subscribe to `conv:<id>` for their own conversation and not another tenant's (UI badge "Realtime").

## Kaggle (after an endpoint exists)
Run `docs/kaggle/enigma_connector_kaggle.ipynb` yourself with Stokastisk build 5, tick its checklist, record results in `docs/TEST_REPORT.md`.
