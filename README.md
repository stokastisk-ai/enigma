# Enigma

Authenticated LLM chat + GPU/vLLM observability console for a self-hosted [Stokastisk](https://github.com/stokastisk-ai/stokastisk) (vLLM 0.25.1) runtime. The GPU host (Kaggle dual T4, for authorised personal use) makes **outbound HTTPS only**; the control plane is Next.js + FastAPI. **Hosting direction is AWS (Cognito, Lambda, PostgreSQL) per `skills.md` section 14; nothing is deployed** — Vercel/Supabase/Clerk notes are historical.

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
python3.13 -m pytest                       # 176 pass, 6 skipped (5 live-Clerk + 1 remote-only GPU) tests skipped unless opted in (incl. SQL/RLS on a throwaway local Postgres)
python3.13 scripts/smoke_e2e.py            # API + mock connector + one chat, all labelled synthetic
cd apps/web && npm ci && npm run build && npm run test:e2e    # 10 mock-mode browser tests (builds separately, needs Google Chrome)
ENIGMA_LIVE_CLERK=1 python3.13 -m pytest tests/live            # real Clerk DEV instance (needs ignored apps/web/.env.local)
cd apps/web && npm run test:e2e:live                           # real sign-up/sign-in/sign-out in a browser
```
Mock data is always labelled **Synthetic** in the UI. Real GPU data requires the Kaggle notebook in `docs/kaggle/`.

**GPU policy: only your user-launched Kaggle 2× T4 runs inference. AWS (when approved) hosts the CPU control plane only; there is no AWS GPU and no hosted-model fallback.** Status: local app verified; real Clerk **development** instance verified; GitHub repo private; Supabase, Vercel and Kaggle GPU are **not** verified (see [BLOCKED_EXTERNAL_SETUP](BLOCKED_EXTERNAL_SETUP.md), [BLOCKED_PRIVATE_ORG_HOBBY](BLOCKED_PRIVATE_ORG_HOBBY.md)).

**AWS visibility (nothing deployed):** CloudWatch dashboard draft + admin-only `/app/admin/aws` page (mock/synthetic until approved), see [AWS_OPERATIONS_DASHBOARD](docs/AWS_OPERATIONS_DASHBOARD.md); real-table DynamoDB parity plan: [AWS_DYNAMODB_REAL_PARITY](docs/AWS_DYNAMODB_REAL_PARITY.md). Track B (DynamoDB) store: `ENIGMA_STORE=dynamodb`, emulator-tested only (`tests/api/test_dynamodb_store.py`). Root-key plan: `docs/AWS_ROOT_KEY_REMEDIATION.md`. AWS cost model: `/usr/local/bin/python3.13 scripts/aws_cost_model.py` (offline, itemized low/moderate/high, Track A vs B). **Deployment awaits your approval of docs/AWS_DEPLOYMENT.md.** AWS: [AWS_COST_MATRIX](docs/AWS_COST_MATRIX.md) · [AWS_ARCHITECTURE](docs/AWS_ARCHITECTURE.md) · [AWS_SECURITY](docs/AWS_SECURITY.md) · [AWS_DEPLOYMENT](docs/AWS_DEPLOYMENT.md) · [AWS_MIGRATION_REPORT](docs/AWS_MIGRATION_REPORT.md). Local checks: `python3.13 tests/sql/run_aws_sql_tests.py`, `cd apps/web && npm run test:unit` (current counts: 317 pytest pass, 6 skipped, 10 browser tests, 15 node unit tests). **Local NVIDIA GPU is never used: tests block GPU libraries and `nvidia-smi`; real GPU runs only on Kaggle.** Use `/usr/local/bin/python3.13`..

Docs: [ARCHITECTURE](docs/ARCHITECTURE.md) · [SECURITY](docs/SECURITY.md) · [DEPLOYMENT](docs/DEPLOYMENT.md) · [SDK_INTEGRATION](docs/SDK_INTEGRATION.md) · [DECISIONS](docs/DECISIONS.md) · [TEST_REPORT](docs/TEST_REPORT.md) · [BLOCKED_EXTERNAL_SETUP](BLOCKED_EXTERNAL_SETUP.md)
