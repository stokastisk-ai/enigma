# Enigma

Authenticated LLM chat + GPU/vLLM observability console for a self-hosted [Stokastisk](https://github.com/stokastisk-ai/stokastisk) (vLLM 0.25.1) runtime. The GPU host (Kaggle dual T4, for authorised personal use) makes **outbound HTTPS only**; the control plane is Next.js + FastAPI on Vercel with Supabase and Clerk.

```
apps/web        Next.js 16 App Router, shadcn/ui, Recharts (BFF at /api/enigma/*)
apps/api        FastAPI (Python 3.13) - short-lived Vercel Functions, no GPU libs
connector/      enigma_connector: GPU-host connector (stdlib urllib + nvidia-ml-py + prometheus_client)
supabase/       migrations: schema, RLS, atomic job/telemetry functions
packages/contracts  generated OpenAPI + TS types
tests/          pytest (API, connector, SQL/RLS), docs/ architecture + report
```

## Local quick start (mock data, no credentials)
```bash
python3.13 -m pip install -r apps/api/requirements-dev.txt nvidia-ml-py prometheus_client   # no venv/uv by policy
python3.13 -m pytest                       # 114 pass, 5 live-Clerk tests skipped unless opted in (incl. SQL/RLS on a throwaway local Postgres)
python3.13 scripts/smoke_e2e.py            # API + mock connector + one chat, all labelled synthetic
cd apps/web && npm ci && npm run build && npm run test:e2e    # 10 mock-mode browser tests (builds separately, needs Google Chrome)
ENIGMA_LIVE_CLERK=1 python3.13 -m pytest tests/live            # real Clerk DEV instance (needs ignored apps/web/.env.local)
cd apps/web && npm run test:e2e:live                           # real sign-up/sign-in/sign-out in a browser
```
Mock data is always labelled **Synthetic** in the UI. Real GPU data requires the Kaggle notebook in `docs/kaggle/`.

Status: local app verified; real Clerk **development** instance verified; GitHub repo private; Supabase, Vercel and Kaggle GPU are **not** verified (see [BLOCKED_EXTERNAL_SETUP](BLOCKED_EXTERNAL_SETUP.md), [BLOCKED_PRIVATE_ORG_HOBBY](BLOCKED_PRIVATE_ORG_HOBBY.md)).

Docs: [ARCHITECTURE](docs/ARCHITECTURE.md) · [SECURITY](docs/SECURITY.md) · [DEPLOYMENT](docs/DEPLOYMENT.md) · [SDK_INTEGRATION](docs/SDK_INTEGRATION.md) · [DECISIONS](docs/DECISIONS.md) · [TEST_REPORT](docs/TEST_REPORT.md) · [BLOCKED_EXTERNAL_SETUP](BLOCKED_EXTERNAL_SETUP.md)
