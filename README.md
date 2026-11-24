# Enigma

Authenticated LLM chat + GPU/vLLM observability console for a self-hosted [Stokastisk](https://github.com/stokastisk-ai/stokastisk) (vLLM 0.25.1) runtime. **All inference runs on a user-launched Kaggle 2× T4 notebook**; the cloud side is a CPU-only control plane that stores jobs, chat history and measured telemetry. The Kaggle connector only makes outbound HTTPS calls, so nothing on the GPU host is exposed.

```
apps/web            Next.js 16 / React 19 App Router, shadcn/ui, Recharts (authenticated BFF at /api/enigma/*)
apps/api            FastAPI (Python 3.13) control plane, no GPU libraries
connector/          enigma_connector: GPU-host connector (stdlib urllib + nvidia-ml-py + prometheus_client)
infra/aws/sql       portable PostgreSQL schema + atomic job functions (the production store)
packages/contracts  generated OpenAPI + TypeScript types
supabase/           legacy Supabase schema (optional store, not used in production)
tests/              pytest (API, connector, SQL), docs/ architecture, security, deployment
```

## Hosting
Railway: `apps/web/Dockerfile` (Next.js standalone) and `apps/api/Dockerfile` (uvicorn) built from the repository root, plus Railway PostgreSQL on the private network. Both app services use Railway Serverless and sleep when idle. Sign-in is Amazon Cognito (a public app client, the app talks to the Cognito IDP API directly; Clerk remains an optional alternative). A daily GitHub Actions job prunes old telemetry (`.github/workflows/retention.yml`). See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Local quick start (mock data, no credentials, CPU only)
```bash
python3.13 -m pip install -r apps/api/requirements-dev.txt nvidia-ml-py prometheus_client   # no venv/uv by policy
python3.13 -m pytest                       # API, connector, SQL on a throwaway local PostgreSQL, governance
python3.13 scripts/smoke_e2e.py            # API + mock connector + one chat, all labelled synthetic
cd apps/web && npm ci && npm run typecheck && npm run lint && npm run test:unit && npm run test:e2e   # mock-mode browser tests (needs Chrome)
```
Mock data is always labelled **Synthetic** in the UI. Real GPU data requires the Kaggle notebook in `docs/kaggle/`.

**GPU policy:** only your user-launched Kaggle 2× T4 runs inference. There is no cloud GPU and no hosted-model fallback; local tests never initialise a GPU (`tests/conftest.py` blocks it).

Docs: [ARCHITECTURE](docs/ARCHITECTURE.md) · [SECURITY](docs/SECURITY.md) · [DEPLOYMENT](docs/DEPLOYMENT.md) · [SDK_INTEGRATION](docs/SDK_INTEGRATION.md) · [DECISIONS](docs/DECISIONS.md) · [TEST_REPORT](docs/TEST_REPORT.md)
