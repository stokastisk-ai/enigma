# Deployment plan and current status

**Nothing is deployed.** Hosted deployment is blocked for this private, commercial, organization-owned project: see `BLOCKED_PRIVATE_ORG_HOBBY.md`. External-setup status and the exact Supabase dashboard steps: `BLOCKED_EXTERNAL_SETUP.md`.

## What runs today (all local, zero spend)
- API: `python3.13 -m uvicorn index:app --app-dir apps/api --port 8787` (in-memory store unless Supabase is configured)
- Web: `cd apps/web && npm run build && npx next start -p 3100` (real Clerk dev instance when `apps/web/.env.local` holds the keys; otherwise the guarded dev login)
- Connector: `python -m enigma_connector --mode mock` (labelled synthetic) or the Kaggle notebook (real GPU, not yet run)

## Environment names (values only in ignored `.env.local` / provider settings; see `.env.example`)
Web public: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `NEXT_PUBLIC_CLERK_SIGN_IN_URL`, `NEXT_PUBLIC_CLERK_SIGN_UP_URL`, `NEXT_PUBLIC_CLERK_*_FALLBACK_REDIRECT_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (legacy `..._ANON_KEY` accepted). Only these are exposed to browsers.
Web server-only: `CLERK_SECRET_KEY`, `ENIGMA_API_URL`.
API server-only: `ENIGMA_ENV`, `ENIGMA_STORE`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CLERK_JWKS_URL`, `CLERK_ISSUER`, `CLERK_AUTHORIZED_PARTIES`, `CRON_SECRET`.
Not used by the code and therefore not required: `DATABASE_URL` (the API talks to Supabase over HTTPS/PostgREST; a direct URL is only for optional migration tooling and must never be deployed), `ENIGMA_RUNTIME_TOKEN_SECRET` (runtime tokens are random 256-bit values stored hashed), any Google/Gemini key.

## Clerk
Test (`pk_test_`) keys are for development domains only. A production instance needs a real domain and explicit approval; no paid add-ons or Organizations are used (Enigma has its own workspace tables). `CLERK_AUTHORIZED_PARTIES` must list the web origin(s); browser tokens carry `azp`.

## Hosting (when authorized)
Two projects keep `/api` routes from colliding: web (root `apps/web`) and API (root `apps/api`, Python 3.13 via `.python-version`, ASGI `app` in `index.py`, daily prune cron in `vercel.json`). Vercel's Python runtime lists 3.12/3.13/3.14. These routing/limit details are **unverified** until a real deployment exists.

## Production safety
The API refuses to start in production without Supabase store, Clerk JWKS/issuer and a cron secret, and refuses dev auth on Vercel or in production. The web proxy fails closed when no auth is configured.
