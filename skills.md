# ENIGMA — Project Charter and Persistent Development Rules

Version: 1.0 | Target directory: `/media/waqasm86/External1/Waqas-Projects/Project-Enigma/`

> This file is the canonical engineering brief. Claude Code automatically reads **CLAUDE.md**, not an arbitrary root `skills.md`. Keep `CLAUDE.md` as a short pointer to this file. A discoverable custom skill lives at `.claude/skills/enigma-build/SKILL.md`. Read this file before any modification, design change, or deployment.

## 1. Product definition

Enigma is an authenticated, multi-tenant, developer-facing **LLM inference playground plus GPU/vLLM observability workspace**. Users launch `stokastisk` in an approved GPU environment, connect the running SDK to Enigma using a scoped token, exchange prompts and streamed model-generated tokens, see live NVIDIA GPU/vLLM metrics, and inspect historical sessions and benchmarks when the GPU is offline. Initial tested compute is Kaggle dual NVIDIA T4 for authorized **personal experimentation**, not an always-on commercial inference backend. Build the BYOC design so other explicitly authorized GPU hosts work later. No Gemini, paid inference API, Go backend, Grafana server, or Kuberenetes requirement.

## 2. Non-negotiable local development policy

1. The project root is exactly `/media/waqasm86/External1/Waqas-Projects/Project-Enigma/`; never scatter Enigma files elsewhere without a documented reason. This policy does not authorize deleting unrelated files outside Enigma.
2. Use the user's **manually installed `python3.13`** for all Python execution, pip installations, tests and FastAPI processes. Check `command -v python3.13` and `python3.13 --version` before running. Examples: `python3.13 -m pip install ...`, `python3.13 -m pytest`, `python3.13 -m uvicorn ...`. If missing, report accurately; do not silently switch Python.
3. **Do not use `uv`, `ruff`, `virtualenv`, `venv`, Poetry, Pipenv, Conda, or `python -m venv`.** Pin dependencies in `requirements.txt` and install with `python3.13 -m pip install -r ...` using the user's existing interpreter. Acknowledge system-wide dependency conflict risks and use `--user` only if needed/approved. NEVER use `--break-system-packages` automatically. Vercel's managed installer is outside local tooling control; do not claim Vercel does not use it internally.
4. Frontend: **Next.js App Router + React + TypeScript + Tailwind CSS + shadcn/ui + selected shadcn.io components**, npm (unless the actual repo already uses a different lockfile), Node matching current Next requirements. Backend: **FastAPI in Python 3.13**, deployed as short-lived Vercel Python Functions, never a daemon or GPU worker. Persistence: Supabase Postgres and protected Supabase Realtime. Identity: Clerk, verified on server. Hosting: Vercel.
5. No Go unless a concrete functional requirement cannot be met in Python/TypeScript and an explicit approval is obtained.
6. **GPU/LLM observability instrumentation only:** `nvidia-ml-py` (import `pynvml`), `prometheus_client`, plus built-in vLLM `/metrics` / Stokastisk reporting. Do not introduce Datadog agents, OpenTelemetry SDKs/exporters, Loki, Tempo, SigNoz, W&B, third-party telemetry agents, or Grafana binaries. FastAPI, requests/httpx, Supabase client, Clerk etc. are allowed for non-observability application functionality. If vLLM transitively ships other monitoring code, do not enable it. Avoid duplicating NVML samples or collecting unsupported counters.
7. Never change, vendor, extract and republish, or rebuild Stokastisk's bundled vLLM/CUDA native libraries just to integrate Enigma. Treat SDK 0.1.0 build 5 as an external pinned binary. Integrate by supported public APIs and authenticated HTTP. Keep Enigma cloud connector modular; submit any desired SDK changes as a separate proposed patch/PR with tests.
8. All user-generated files and large artifacts stay outside Git. `.env*` with secrets, API credentials, model weights, notebook caches, `.next`, `node_modules`, `__pycache__`, `.pytest_cache`, `.vercel`, wheel files, SDK archives, logs, reports and telemetry dumps must be gitignored. Keep `.env.example` and small synthetic fixtures.
9. After each completed task, remove **only positively identified, task-created and unnecessary** temporary files under this Enigma root (extracted reference repos, downloaded zips, scratch dumps, obsolete generated reports). Preserve user files, untracked edits, user downloads, source code, migrations, lockfiles, evidence and all files of uncertain provenance. Inspect `git status --short`, record intended deletions, and avoid blanket `rm -rf`, automated deletion of unknown files, or destructive `git clean -fdx`. If uncertain, ask rather than delete.
10. Keep architecture lean: no duplicate frameworks, databases, UI libraries, CSS systems or chart engines. Prefer reusable components and one canonical implementation. Do not retain entire Grafana checkout or Anthropic skills repo inside Enigma. Local references can be inspected in place or from a temporary extraction that is cleaned safely.
11. Work incrementally, actually execute builds/tests and report commands/results honestly. Never claim successful Kaggle, Vercel, Clerk or Supabase integration without real credentials and verification. No simulated measurements labelled live.
12. Never print secrets or commit production credentials. No exposed privileged Supabase service-role keys, tunnel secrets, Hugging Face tokens or runtime API keys in browser bundles, logs or Git.

## 3. Verified source baseline (uploaded archives; revalidate any changes)

- SDK repository: `https://github.com/stokastisk-ai/stokastisk` and docs `https://github.com/stokastisk-ai/stokastisk-ai.github.io`.
- Target SDK `stokastisk 0.1.0` **wheel build 5**, Python **3.13**, bundled `vLLM 0.25.1`, CUDA 13 / SM75 / `TRITON_ATTN` / FP16 on Kaggle dual T4; wheel SHA256 `fe80d7c36fa78cf8a26feb1e00bb5f1f006ca45a027450353603976320ae0b3e`. CPU audit supports build 5; **build 5 was NOT independently exercised on Kaggle**.
- Demonstrated models/modes from earlier build 3 evidence: `Qwen/Qwen2.5-0.5B-Instruct` TP=1 and other tested modes; `Qwen/Qwen2.5-3B-Instruct` TP=2/PP=2; `Qwen/Qwen2.5-Coder-7B-Instruct` TP=2/PP=2. Tested context `max_model_len=2048`; do not advertise Llama-3 or other models as verified without actual runtime evidence.
- `sdk/stokastisk/serving.py` exposes `ServerProcess`, `chat`, `stream_chat`; server `/health`, `/v1/chat/completions`, `/metrics`; **HTTP server acceptance demonstrated Qwen2.5-0.5B TP=1 on earlier build 3**, not all other serving configurations.
- SDK GPU sampler: `sdk/stokastisk/telemetry/nvml.py`; read existing `topology/nvml.py` to avoid double NVML initialization. The sampler often uses `nvidia-smi` and optional NVML, with explicit `None` for missing counters. Enigma's new exporter should use `nvidia-ml-py` directly as user requested and **not assume all GPU metrics are supported**.
- Uploaded `nvidia-ml-py` wheel is `13.615.71` and exposes `pynvml.py`; uploaded `prometheus/client_python` contains exposition, collector, metric and parser facilities. New code should respect actual installed APIs; `prometheus_client` instrumentations are metric producers, **not** a Prometheus server, durable storage, or remote write collector.
- Uploaded `grafana-main.zip` (≈29k files) is **Grafana core**, not Ask O11y. Use for layout/information-architecture inspiration only; upstream is **AGPL-3.0-only** by default. Avoid copying significant source. No Grafana server required. Relevant reference patterns: `public/app/features/explore/`, `public/app/features/browse-dashboards/` and dashboards/panel configuration code. There is no guaranteed standalone free chat UI in this archive.
- Uploaded Anthropic `skills-main.zip` uses `skills/<name>/SKILL.md` with YAML metadata; relevant concepts: `skills/skill-creator`, `skills/frontend-design`, `skills/webapp-testing`. Do not wholesale import the entire skills repository. Do not confuse a markdown brief with a Claude Code skill.
- `https://www.shadcn.io/` is **independent of** `https://ui.shadcn.com/`; check licenses, npm dependencies, current install commands, SSR compatibility and source quality **per component**. `/charts` currently includes both TanStack-based chart variants and some Recharts-based variants. Pick **one** chart engine for MVP (default Recharts + standard shadcn ChartContainer). `/ui/chat` is a chat scroll-oriented component/pattern, not a functioning LLM backend.

## 4. Architecture and deployment boundaries

```
Browser (Next.js on Vercel)
  | Clerk-authenticated requests / protected subscriptions
  v
Next.js routes (BFF) + FastAPI v1 API (short-lived Vercel Functions)
  |                  |
  |                  +--> Supabase Postgres: projects, runtimes, runs, jobs, messages, metrics
  |                  +--> Supabase Realtime private channels: ephemeral token deltas/status
  |
  v
Enigma cloud control plane (job lease + authenticated event ingestion)
  ^  outbound HTTPS polling and batched uploads, NO inbound Kaggle ports
  |
Stokastisk connector inside authorized Kaggle notebook / compatible GPU host
  |-- pinned stokastisk.ServerProcess on 127.0.0.1:8000
  |-- vLLM built-in /metrics scraped LOCALLY, parsed with prometheus_client.parser
  |-- nvidia-ml-py GPU sampler (bounded frequency, handles missing counters)
  |-- jobs execute locally and stream token deltas *outbound* to cloud
  `-- heartbeat, metrics, final result, cancellation checking
```

### Key decisions

- **Kaggle is ephemeral and subject to quota/terms**; never design around 24/7 availability. Confirm current Kaggle ToS for external real-time access; strictly personal/demo use unless authorized. Provide offline historical workspace and a portable connector for legitimate commercial GPU hosts.
- **Default MVP transport = authenticated outbound HTTPS long-polling/short-polling from Kaggle**, with idempotent job lease and sequence-numbered token/result events; protected Supabase Realtime broadcast to browser for a near-real-time experience. You MAY investigate named Cloudflare Tunnel for controlled demonstration but it is not required and Quick Tunnels do not support SSE. Do not publicly bind vLLM, export private credentials, or depend on persistent Kaggle inbound connections.
- FastAPI deployed on Vercel **cannot host an infinite background worker, in-process queue or long-running GPU session**. All durable jobs/status and leases live in Postgres. Every endpoint must complete promptly, including polling/heartbeat endpoints. Python 3.13 `api/index.py` or validated Vercel-native FastAPI layout; test single-project routing for `/api/v1` and avoid accidentally conflicting Next.js `/api` paths. Prefer Next.js BFF for browser auth and FastAPI only for SDK API. Document whether separate Vercel projects are needed; use stable same-origin proxy routes when feasible.
- No requirement for full Prometheus/Grafana server. Parse locally exposed vLLM Prometheus format into compact, allowlisted samples and send JSON batches; maintain `prometheus_client` counters/gauges/histograms for Enigma-specific metadata and expose a localhost `/metrics` if useful. Avoid using unsanitized `prometheus_client` multiprocess mode on multiple vLLM workers. Supabase stores **bounded sampled data**; dashboard aggregation and downsampling handle growth. If true PromQL or long-retention TSDB becomes necessary, defer to later stage.
- For streamed chat: server-side authenticated submission -> persist job -> GPU connector claims job -> POST ordered token deltas/batches -> authorized private Realtime broadcast (or short polling fallback) -> browser renders incremental text. Persist final assistant message once, not one DB row per token. Bound latency through configurable small batching interval; measure actual p50/p95 end-to-end delay rather than promise sub-second performance.
- Handle duplicate deliveries, reconnect, stale worker, expired lease, cancellation, terminal status, user switching projects, offline GPU, provider errors, GPU OOM, and model load time. Separate `run_id`, `job_id`, `conversation_id`, `request_id`, `runtime_id`, `event_seq` consistently.
- A model's inference output is **not** evidence of GPU health. GPU charts must come from verified telemetry; LLM explanations must reference actual observed metrics and time windows, state uncertainty, and never fabricate numbers. Stage natural-language-to-metric-query features AFTER measurement correctness.

## 5. Required scope of MVP

### Routes and UX

- Landing, auth / sign in, onboarding, projects, workspace dashboard, `runtimes`, `inference` overview, `gpus`, `runs`, `playground/chat`, `settings/api-keys`.
- Shell inspired by Grafana navigation, not copied: left collapsible nav, top project/runtime selector, time range, online/offline banner, dashboard grid, hover/focus accessible tooltips, dark/light mode, responsive layouts, dense metric cards, charts, run comparison table, JSON export, keyboard-friendly chat.
- Chat: conversation history, model/runtime selector, prompt input, streaming token deltas, cancel, retry, error states, empty/offline states, request diagnostics: TTFT, e2e latency, output tokens, throughput (only where measurable), TP/PP, context and selected GPU.
- GPU: sampled GPU0/GPU1 utilization, VRAM used/total, temperature, power, optionally PCIe throughput (show N/A when unavailable), clearly labelled timestamps and measured-vs-unavailable.
- vLLM: parse and visualize only confirmed `/metrics` names (e.g., `vllm:time_to_first_token_seconds`, `vllm:e2e_request_latency_seconds`, `vllm:inter_token_latency_seconds`, `vllm:kv_cache_usage_perc`, `vllm:num_requests_running`, `vllm:num_requests_waiting`, `vllm:prompt_tokens`, `vllm:generation_tokens`, `vllm:num_preemptions`, and `vllm:request_success`) with proper histogram/counter handling and version compatibility.
- Charts: line/area live metrics; status cards; sparklines; stacked bars for GPU capacity; histogram/latency percentiles where histogram buckets permit; comparative bar charts for TP/PP; sortable/filterable tables. Do not fabricate gauge/counter transformations.

### Proposed schema (migrations must implement RLS)

- `organizations` or `workspaces`, `memberships`, `projects`, `runtime_credentials` (only hashed token + prefix + rotation/revocation), `runtimes` (heartbeat + capability and binary identity), `models`, `inference_runs`, `conversations`, `messages`, `inference_jobs` (status, lease_owner, lease_expires_at, attempt, idempotency_key), `inference_events` (bounded sequence replay), `gpu_samples`, `vllm_metric_samples`, `audit_events`.
- Every organization-scoped row must include `workspace_id` and project ownership. RLS: private by default; auth via Clerk token validated as Supabase third-party auth provider, *plus* explicit workspace membership; server privileged operations use server-only credentials and still enforce ownership before queries. Never trust client-supplied `workspace_id`, never use shared public Realtime channels, never publish service role or unscoped SDK tokens.
- Constraints/indexes on `(workspace_id, project_id, time)`, `(runtime_id, captured_at)`, `(job_id, event_seq unique)`, `(runtime_id, last_heartbeat_at)`, `(workspace_id, idempotency_key)`. Implement bounded retention, size limits, and cleanup strategy (scheduled one-shot cleanup allowed; no eternal process).

### API contract — design and test before implementation

- `POST /api/v1/runtimes/register` (SDK auth; return runtime identity/capabilities)
- `POST /api/v1/runtimes/{runtime_id}/heartbeat`
- `POST /api/v1/telemetry/batch` (timestamps, GPU device IDs, metric allowlist, units, provenance, idempotency)
- `POST /api/v1/jobs/claim` (short, safe lease, auth; no job for wrong workspace/runtime)
- `POST /api/v1/jobs/{job_id}/events` (ordered token batches with event_seq)
- `POST /api/v1/jobs/{job_id}/complete` and `/fail`
- `POST /api/v1/jobs/{job_id}/ack-cancel` and `GET /api/v1/jobs/{job_id}/status`
- Browser-facing authenticated Next.js routes for job submission, cancellation, conversation CRUD, time-bounded metrics, project CRUD and token issuance/revocation. Route names can be refined but MUST be documented in a central OpenAPI schema and client types.
- Require explicit request size, rate, concurrency and token budget caps. Validate JSON with Pydantic on FastAPI and Zod/TypeScript at browser boundary. Return standardized error shape and correlation IDs; do not log raw prompts by default.

## 6. UI design system and shadcn component selection

**Understand the sites:** `https://ui.shadcn.com/docs`, `https://www.shadcn.io`, `https://www.shadcn.io/charts`, `https://www.shadcn.io/ui/chat` and AI components index. Use current documentation and check license/source for each installed component. Prefer canonical shadcn imports rather than copying a whole showcase site.

| UI requirement | Component/pattern | Priority |
|---|---|---|
| Sidebar, workspace nav | Sidebar, Breadcrumb, Dropdown Menu, Sheet, Command | P0 |
| Model/run control | Select, Tabs, Combobox, Popover | P0 |
| Metric summary | Card, Badge, Skeleton, Tooltip, Separator | P0 |
| Live charts | ChartContainer, line/area/bar, tooltip, legend, brush/time-window | P0 |
| Run and request history | Table/DataTable, pagination, filters | P0 |
| LLM chat | Scroll-area/message-scroller, Message, Prompt Input or Textarea, button, code block | P0 |
| Alerts and feedback | Sonner/Toast, Alert, Dialog, Progress | P0 |
| Authenticated onboarding | Form, Input, Label, Dialog, Copy button | P0 |
| Comparison and diagnostics | Resizable panels, Accordion, Collapsible, Tabs | P1 |
| Markdown and syntax | Safe markdown/code renderer with XSS protections | P1 |
| AI reasoning/tools/canvas | Reasoning, Agent, Artifact, Canvas | Post-MVP only |

Use ONE chart system at first: default **Recharts via canonical shadcn ChartContainer**, as it is straightforward for time-series and avoids loading TanStack Charts and Recharts together. On `/charts` consider `live-line`, `line`, `area`, `bar`, tooltip and crosshair patterns, but verify their implementation and install dependencies. Ensure no misleading percent/time units and avoid expensive chart rerenders; use bounded windowing and throttled updates.

Frontend design direction: purpose-built **technical observability console**, restrained dark mode by default, contrasting restrained accent (not a Grafana clone), consistent typography, explicit UTC/local timestamp choice, well-labelled 'offline', 'stale', 'not available' states, accessible contrast, responsive mobile drawer; no ornamental widgets that conceal data.

## 7. Python connector design

- The GPU-facing connector should be a small optional integration layer which invokes the **public** `stokastisk.serving.ServerProcess` and `serving.stream_chat` APIs; should not import or execute vLLM directly or use a second attention implementation.
- Use `nvidia-ml-py` (`pynvml`) for local GPU inventory and sampling, gracefully handling `NVMLError`, incompatible drivers, lack of permission, and unavailable optional sensors; initialize and shutdown NVML responsibly, safely map GPU index to device UUID if available.
- Use `prometheus_client` for application counters/gauges/histograms and `prometheus_client.parser.text_string_to_metric_families` to parse built-in vLLM `/metrics` exposition. Avoid global high-cardinality labels (raw prompt/model path/trace/request ID); use capped stable dimensions (runtime/model/worker/GPU).
- Sampling default 1-second GPU cadence, 5-second batch upload; limits configurable. vLLM histogram values are cumulative; calculate rates/quantiles correctly and handle restarts/counter reset. Mark unavailable values as null; never convert missing to 0.
- Create an offline upload spool with bounded size, retry/backoff/jitter, idempotency and flush on reconnect. Do not exhaust Kaggle disk/internet. Only authorized notebook initiation; no hidden autostart or scraping outside an explicit run.
- Model config: FP16; `max_model_len<=2048` unless separately verified; default TP=1 for Qwen2.5-0.5B server; allow TP=2 only for individually validated configurations. Never imply 2 GPUs double token throughput. Prompt context budgeting includes chat template tokens and output maximum.
- Maintain SDK and backend environment separation: Kaggle has its own Python vLLM dependencies; **do not install Stokastisk/vLLM/GPU libs into Vercel FastAPI**, which only handles lightweight API/DB functions.

## 8. Testing and acceptance gates

1. Repo scan and exact environment inventory before installation. Clear architecture/ADR and schema, no claimed external setup without proof.
2. Python unit: NVML unavailable, malformed metrics, counter resets, timestamps, spool and token redaction; `python3.13 -m pytest`.
3. FastAPI integration: auth positive/negative, workspace-crossing blocked, event ordering/idempotency, stale leases, cancellation, retry, rate caps, payload limits; FastAPI TestClient with fake GPU endpoint.
4. Database: test RLS for two separate Clerk identities/workspaces; prove no cross-tenant read/write/subscribe. Test with Supabase local/remote only when credentials available.
5. Frontend: TypeScript compile, lint (ESLint), production build, chat streaming/resume, charts, responsive layout, accessibility, offline state, Playwright E2E. Prefer existing Anthropic webapp-testing skill if available.
6. Integration using mock connector first: browser -> Vercel -> job -> outbound Python poll -> token batch -> private Realtime/poll fallback -> browser, final metrics persistent. Reconnection and stale runtime scenarios.
7. GPU acceptance (only if authorized GPU environment available): validate exact build-5 installation and GPU 0/1, `/health`, real `/metrics`, basic Qwen0.5B TP=1 chat stream, metric timestamp correlation, GPU shutdown and runtime offline behavior. Clearly mark larger TP=2 HTTP serving unverified pending tests.
8. Vercel preview deployment: verify FastAPI routing, 3.13 build, function duration/response limits, no accidentally bundled CUDA wheel, frontend environment variables, Clerk auth callback domains, Supabase RLS, and real chat end-to-end. Without external credentials produce `BLOCKED_EXTERNAL_SETUP.md` with exact manual steps, not fake success.
9. Performance: bounded sample storage, <=5sec dashboard freshness as **goal**, token stream responsiveness measured (not guaranteed), backpressure and disconnected runtime UX.
10. After every phase provide file changes, commands/tests/results, blockers, safe cleanup, next concrete step. Never imply a phase is done if build or acceptance failed.

## 9. Deliverables for Claude Code

- Actual lean monorepo (`apps/web`, `apps/api`, `packages/contracts`, `connector`, `supabase/migrations`, `tests`, `docs`), not copied Grafana source.
- Running local web and FastAPI mock end-to-end, documented production Vercel split/deployment, `.env.example`, `.gitignore`, minimal READMEs and one end-to-end smoke script.
- Secure Supabase SQL migrations/RLS tests; Clerk integration; typed API request/response and Pydantic schemas.
- GPU/vLLM observability with only `nvidia-ml-py`, `prometheus_client`, vLLM native metrics; real-time chat from Stokastisk through controlled outbound relay, where environment permits.
- Concrete `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/DEPLOYMENT.md`, `docs/SDK_INTEGRATION.md`, `docs/DECISIONS.md`, `docs/TEST_REPORT.md`.
- No accidental shipping of vendor archives, CUDA binaries, GPUs/models, copied Grafana UI or unlicensed shadcn components.

## 10. Explicit non-goals and red lines

- Do not represent Kaggle GPUs as guaranteed 24/7 inference capacity. Confirm terms before any commercial use; use authorized hosts for commercial pilot.
- Do not attempt to circumvent provider rate, session, network or quota restrictions.
- No public, unauthenticated vLLM server; no unvalidated remote model request; no unbounded inference workloads.
- No in-process permanent Vercel polling worker, no streaming over Cloudflare Quick Tunnel.
- No Go, `uv`, `ruff`, virtual environments, Docker or Kubernetes mandatory dependencies.
- Do not do blanket removal of other people's files under Enigma.
- Do not claim `skills.md` is auto-loaded by Claude Code; load via `CLAUDE.md` and `.claude/skills/enigma-build/SKILL.md`.

## 11. Links to recheck before implementation

- SDK: https://github.com/stokastisk-ai/stokastisk
- SDK release: https://github.com/stokastisk-ai/stokastisk/releases/tag/v0.1.0
- Documentation: https://github.com/stokastisk-ai/stokastisk-ai.github.io
- Grafana reference: https://github.com/grafana/grafana
- Anthropic skill patterns: https://github.com/anthropics/skills
- shadcn: https://ui.shadcn.com/ , https://www.shadcn.io/charts , https://www.shadcn.io/ui/chat
- FastAPI on Vercel: https://vercel.com/docs/frameworks/backend/fastapi
- Clerk-Supabase: https://clerk.com/docs/guides/development/integrations/databases/supabase
- Supabase RLS: https://supabase.com/docs/guides/database/postgres/row-level-security
- Cloudflare Quick Tunnel SSE caveat: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/

---

# 12. Enigma closed-source ownership, GitHub and zero-spend deployment policy (revision 2026-11-21)

This section supersedes conflicting prior instructions. Enigma is **proprietary, closed-source business software**. Its canonical GitHub remote is `https://github.com/stokastisk-ai/enigma.git`, which MUST remain private. Never change visibility, mirror to a public repository, publish code in a gist, or add an open-source license without explicit authorization. Code from Grafana (default AGPL) and non-permissively licensed shadcn.io packages must not be imported. The Apache-2.0 Stokastisk project is separate; SDK and platform have distinct licenses. Do not assume that a private repository prevents credential leaks.

## Mandatory secret-handling policy

The owner supplied existing local PostgreSQL connection strings, a Clerk test publishable key and secret, Supabase project URL and publishable key, and a Google API key in conversation. **NEVER copy their raw values** into this file, CLAUDE.md, prompts, committed configuration, code, tests, CI logs, browser output, documentation or GitHub issues. Treat previously exposed private credentials (database password, Clerk secret, Google API key) as compromised and request rotation using their provider dashboards before live deployment. Do not reuse the Google key; Enigma uses Stokastisk, not Gemini. Public identifiers and publishable keys are less sensitive but still belong in environment files, not permanent memory. The provided local PostgreSQL `agentic_workspace` database is NOT Enigma's remote Supabase connection and is not reachable from Vercel using localhost. Never migrate or delete this existing database; use it only for authorized local testing after reviewing the target schema. Avoid overwriting any existing `.env`.

Mandatory configuration *names* (values supplied only through local ignored `.env.local`/`.env` or official encrypted provider settings):

```
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=
CLERK_SECRET_KEY=
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up
NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL=/
NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL=/
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
DATABASE_URL=
DIRECT_URL=
ENIGMA_RUNTIME_TOKEN_SECRET=
ENIGMA_API_BASE_URL=
ENIGMA_ALLOWED_ORIGINS=
```

Check actual code and docs for additional required names (`CLERK_JWKS_URL`, Clerk issuer/audience, etc.) and use exact existing identifiers; never invent secrets. Only `NEXT_PUBLIC_*` values may be exposed to client bundles; **NEVER** give service-role, database, Clerk secret or runtime token to the browser, Kaggle notebook output, or Git repository. `ENIGMA_RUNTIME_TOKEN_SECRET` is illustrative; prefer existing token issuer/key naming and hash at rest. Avoid unnecessary variables; don't add `GOOGLE_GENERATIVE_AI_API_KEY` to Enigma. Vercel secrets must be scoped correctly by environment; use the Vercel dashboard or an authenticated CLI to add their actual values interactively, not arguments that land in shell history. Inspect Git history and worktree for previously committed secrets before pushing; use a secret scanner if available and review results without printing secrets.

## Verified constraints for zero-spend deployment (2026-11-21)

1. Vercel Hobby is restricted to **personal, non-commercial** use. A real commercial, revenue-seeking Enigma deployment is NOT eligible for Hobby; do not work around this restriction or claim zero-cost commercial hosting. An eligible strictly personal proof-of-concept is possible subject to quotas. Source: https://vercel.com/docs/plans/hobby and https://vercel.com/docs/limits/fair-use-guidelines.
2. Vercel Hobby does **not** support importing/deploying a private GitHub **organization-owned** repository (including `stokastisk-ai/enigma`) via normal Git integration. This is a hard blocker for the requested direct private-org → Hobby workflow. Do not make the repository public, relocate ownership, create a personal mirror, use unauthorized CI/API loopholes, or buy a subscription without approval. Preserve GitHub as private source of truth; report the blocker and continue local integration and provider readiness. Check official documentation before testing any supported alternative path, and do not assert a workaround is permitted. Source: https://vercel.com/docs/git and https://vercel.com/docs/limits.
3. Vercel supports Python 3.13 FastAPI Functions. Its serverless lifecycle is unsuitable for permanent polling workers or running GPU inference. Source: https://vercel.com/docs/functions/runtimes/python.
4. Supabase Free: at most two free projects per organization/account entitlement as documented, 500 MB database quota per project, possible automatic pausing after ~one week of inactivity, plus separate Realtime/egress limits. Verify actual account usage and limits before any migration. Source: https://supabase.com/pricing and https://supabase.com/docs/guides/platform/free-project-pausing. Never artificially generate activity to bypass usage policies.
5. Clerk Free has stated usage allowances but feature caps and billing rules can change. Confirm organization/member features and active account entitlement in https://clerk.com/pricing before enabling; do not add paid add-ons or payment method. Never infer the supplied `pk_test_` key is suitable for production; configure provider-approved production keys only when permitted.
6. Kaggle's dual T4 environment is an ephemeral, quota-limited, personally authorized development GPU, not a commercial customer inference backend. Enigma must show *offline* when stopped. Do not extend sessions or bypass platform policies.
7. No purchases, billing upgrades, paid add-ons, license purchases, chargeable resources, subscription activation, or payment card enrollment without explicit user approval. Stop at the specific provider/paywall blocker and document it accurately.

## GitHub checkpoint workflow (never bypass privacy checks)

Before push:
```
cd /media/waqasm86/External1/Waqas-Projects/Project-Enigma/
git status --short
git remote -v
git branch --show-current
git ls-files
git check-ignore apps/web/node_modules .env .env.local
```
Verify repository identity and **private** visibility using authenticated `gh repo view stokastisk-ai/enigma --json nameWithOwner,isPrivate,viewerPermission` if `gh` is installed/authenticated, or GitHub web UI/API with explicit authenticated access. A 404 can mean missing permission; do not assume public/nonexistent. Verify remote URL exactly, no unknown remotes. Review staged changes, remove secrets from index and Git *history* if discovered (stop and request approval before rewriting history), preserve existing commits. Commit only application source, tests, SQL migrations, lockfiles, instruction docs and small synthetic fixtures. Never stage `node_modules`, `.next`, `.vercel`, `.env*`, secrets, caches, wheels, archives, models, local telemetry or database dumps. Use `git add` with explicit paths instead of `git add -A` until audited. Commit to an appropriate reviewed branch and push with `git push -u origin <branch>` only after verifying authorized access and user intent; do not force push. Confirm GitHub remote contains intended commit and remains private. If auth is not present, give exact instructions; do not claim upload complete.

## Lean workspace target and cleanup

The screenshot shows **574.4 MiB**, **49,799 items** in the Enigma directory; this is a snapshot, not a current measurement. The recursive listing includes `apps/web/node_modules`, many package subdirectories, and a small `.git` object store. Claude's local report claims `node_modules` 714 MB on its own measurement; reconcile differing totals with `du` and timestamps instead of treating either number as exact current usage. `node_modules` is a *regenerable development dependency*, not a code defect. The important metric for a private GitHub/Vercel source upload is the **tracked source tree**, not local `node_modules` size.

Measure first:
```
du -sh . .git apps/web/node_modules apps/web/.next 2>/dev/null || true
find . -type f -size +20M -not -path './.git/*' -not -path './apps/web/node_modules/*' -print
find . -name '.env*' -not -name '.env.example' -print
git status --short
git ls-files | wc -l
```

Safe candidate cleanup is **conditional**: if node_modules is not actively needed, and a tested `apps/web/package-lock.json` exists, `apps/web/node_modules` may be removed *only with explicit confirmation* and reinstated with `cd apps/web && npm ci`. `.next`, Playwright test artifacts and `.pytest_cache` may be removed when no process uses them and they are verified generated by the task. NEVER delete `.git`, lockfiles, `supabase/migrations`, source, tests, `docs/kaggle`, `skills.md`, `CLAUDE.md`, the `.claude` directory, unknown files, or unrelated user artifacts. Never use `git clean -fdx` or wildcard `rm -rf`. Don't install system-wide extra tools to optimize cleanup.

## Deployment sequence, gates and documentation

A. Run local tests from documented README: `python3.13 -m pytest`, `python3.13 scripts/smoke_e2e.py`, `cd apps/web && npm ci && npm run build && npm run test:e2e`. First reconcile the discrepancy between README's **111** pytest tests and Claude CLI's **112**; state exact fresh counts. Existing test claims are *not* live provider validation.

B. Rotate compromised credentials, prepare ignored environment files, run git secret audit, verify Clerk test vs production mode, inspect existing configuration/Prisma usage rather than transplant settings from `agentic-workspace-saas`. `DIRECT_URL` should point to a proper Supabase Postgres direct connection only when network and provider support permit, and must not reference localhost for Vercel.

C. On an approved Supabase Free project, inspect its identity and current schema, apply Enigma-specific SQL migrations cautiously, and test RLS, Realtime private channels and two tenants against that real project. Never run these migrations against the unrelated `agentic_workspace` database or overwrite existing tables. Prefer a dedicated Enigma project within free entitlement. Confirm hosted credentials and project region via official dashboard.

D. Configure Clerk Free app and Next.js domain/callbacks. Verify actual Clerk JWKS, sign-in, sign-up, logout, two-user isolation and Clerk→Supabase JWT integration in a live test. Do not rely on synthetic/generated JWKS as evidence of production auth.

E. Evaluate eligible Vercel deployment **without changing private repository visibility or buying anything**. If Hobby + private GitHub org remains disallowed, do not attempt direct integration. Document `BLOCKED_PRIVATE_ORG_HOBBY.md` with official URL, present viable compliant choices (personal local-only demo while holding private org repo, or later authorized paid/commercial deployment) and stop that phase. If an explicitly supported, policy-compliant no-cost deployment is confirmed for a strictly personal prototype, validate all routing, env, build limits and FastAPI 3.13 end-to-end before claiming success.

F. Only after a reachable and authorized endpoint exists, run `docs/kaggle/enigma_connector_kaggle.ipynb` under the user's personal Kaggle session with Stokastisk wheel build 5, and test real T4 metrics, real model-generated chat chunks, runtime disconnect and historical replay. Do not label mock data live.

G. Update `README.md`, `docs/TEST_REPORT.md`, `docs/DEPLOYMENT.md`, and `BLOCKED_EXTERNAL_SETUP.md` with exact results; include source commit/URL, environment names only, test commands, free-tier quota status, and named blockers. Never claim success for steps not executed.

---

# 13. Verified results log (2026-11-21, factual; no secret values)

- Local validation passed on a fresh `npm ci`: pytest 114 pass (+5 opt-in live tests), SQL 78 checks plus a 25-check rollback-only hosted verifier, 10 mock browser tests, typecheck/lint clean, production build ok, `npm audit --omit=dev` 0 vulnerabilities.
- README's earlier "111" vs "112" was a stale count: one more connector test (auto spool cleanup) was added afterwards. Current default total is 114 plus 5 skipped.
- A dedicated Clerk **development** app "Enigma (dev)" was created with the owner's logged-in Clerk CLI. Keys live only in ignored `apps/web/.env.local`. Live-verified: real JWKS/RS256 verification, sign-up with test email + code, sign-in, sign-out, BFF with real session tokens (including `azp`), two-user isolation. Test users were deleted afterwards. Clerk -> Supabase JWT, production instance, and Supabase were NOT tested.
- Correction to Section 12 constraint 1: the Hobby personal/non-commercial wording is in the Vercel **Terms of Service section 4**, not on `/docs/plans/hobby` or the fair-use page. The private-organization restriction is in https://vercel.com/docs/git ("Using Hobby teams"). Both still apply; see `BLOCKED_PRIVATE_ORG_HOBBY.md`.
- Clerk's API edge rejects Python's default `urllib` User-Agent (HTTP 403); live tests send an explicit one.
- `next build/start` read `apps/web/.env.local`; the mock E2E suite therefore builds into `.next-e2e` with Clerk variables blanked.
- Backup patterns (`*.backup-*`) are now git-ignored.


---

# 14. AWS-only hosting migration charter — AUTHORITATIVE OVERRIDE (2026-11-21)

**Precedence:** This section supersedes conflicting Vercel/Supabase/Clerk infrastructure directives in Sections 1–13. Retain previous sections for historical reasoning and original acceptance criteria. `CLAUDE.md` must load this entire `skills.md` and specifically this AWS override. Existing app code is NOT already migrated. Do not call the migration complete without real AWS verification.

## 14.1 Product, repository and local environment

- **Product:** Enigma, a proprietary multi-tenant Next.js/shadcn observability and chat workspace for Stokastisk SDK 0.1.0 build 5, vLLM 0.25.1 and 2 × T4 GPUs in personal authorized Kaggle sessions. SDK remains independent. Commercial inference must use an explicitly authorized non-Kaggle GPU host; Kaggle availability is ephemeral.
- **Repository:** `https://github.com/stokastisk-ai/enigma.git` PRIVATE; do not make public, transfer, fork to public, commit vendor source, or add open-source license. Preserve Git history and existing application. No force pushes.
- **Path:** `/media/waqasm86/External1/Waqas-Projects/Project-Enigma/` on Ubuntu 24. `aws` executable at `/home/waqasm86/.local/bin/aws` (reported CLI v2.36.40) is a CLI, not a Python project interpreter. Use **manually installed `python3.13` and `python3.13 -m pip`** for all local Python tasks; no `uv`, `ruff`, `venv`, `virtualenv`, Poetry, Pipenv or Conda. No automatic `--break-system-packages`. Keep npm lockfile and existing frontend.
- **GPU observability instruments ONLY:** `nvidia-ml-py` / `pynvml`, `prometheus_client` (including parser), plus Stokastisk/vLLM-native metrics. No OpenTelemetry, Grafana binaries, external observability agents, Go, or additional GPU instrumentation. AWS CloudWatch logs for *hosting operations* may be used sparingly; it is not a substitute GPU-inference telemetry collector.
- **Pre-existing tests:** 114 local Python tests reported passed; 5 opt-in live tests skipped; 10 mock browser tests; local SQL 78 checks and rollback verifier 25 checks. The new AWS auth/data backends must pass new live isolation tests; prior Supabase-shim tests alone are not AWS verification.
- **Disk baseline:** 718 MiB total, 714 MiB `apps/web/node_modules`, 1.9 MiB `.git`, 157 Git-tracked files; `CLAUDE_CLI_PROMPT.md` and `README_INSTALL.md` untracked. `node_modules` is reproducible, not to be committed. Clean only positively identified generated task artifacts, NEVER unknown/untracked files, migrations, lockfiles, credentials, git history or SDK binaries. Obtain authorization before deleting dependencies. Preserve `.gitignore` patterns.

## 14.2 Absolute AWS cost/authorization gate (execute BEFORE writes)

**Never equate AWS Free Tier listing, 'always free', service's no additional control-plane charge, or promotional credits with a guaranteed $0 deployment.** Read the two uploaded source lists as historical inputs, verify official live prices/quotas/account applicability by region. User demands only $0 *out-of-pocket*, no paid subscriptions; no purchases, plan upgrade, credit-card charges, budget creation that incurs a charge, or unexpected resource spending. AWS Free plan may end after 6 months or credit exhaustion; an account can be on PAID plan even with unused credits. A prior local account diagnostic reported a PAID plan and $99.99 credits, so **do not assume Free plan**. AWS Free/credit status must be checked live, not inferred from account age or those old logs. Credits are finite and service/region-specific. If PAID plan, budget alert alone does NOT cap spending; do not provision without a verified $0 cost guarantee or explicit user authorization of a bounded risk/budget.

Read-only initial commands (never disclose access keys, tokens or complete account identifiers in logs):
```
aws --version
aws configure list-profiles
aws sts get-caller-identity --query '{Arn:Arn}' --output json
aws configure get region
aws account get-account-information --query 'AccountState' --output text  # skip if unsupported/AccessDenied
aws free-tier get-free-tier-usage --output json  # skip if unsupported; output could be large
```
Inspect AWS Billing console for account plan Free/Paid, credit balance, credit expiration date, Free Tier availability, resource inventory, region prices, and payment exposure. AWS CLI result alone does not establish plan/credits. Use one explicitly approved region, never create a second region silently. Prepare `docs/AWS_COST_MATRIX.md` with per-resource: service, type, region, free monthly quota, trial/credit usage, dependency charges, projected utilization, estimated cost, current account applicability, source URL, outcome `ELIGIBLE/UNKNOWN/BLOCKED`. Default to `BLOCKED` if unknown. Set AWS Budgets/Free Tier alerts only if user approves any change and verified cost is $0; alerts are not hard caps. Record teardown plan before provisioning. Prefer read-only audit, local refactor, and AWS dry-run/template validation first.

No new IAM users/access keys; use existing safe credential chain/profile. Prefer IAM roles and short-lived credentials. Never print env values or run `aws configure` with credentials in command arguments. Avoid saving secrets in CloudFormation parameter defaults or GitHub workflows. All AWS deployments require exact resource inventory and user approval of cost risk; writing the instruction files is NOT authorization to provision AWS resources.

## 14.3 AWS architecture: two assessed tracks, explicit decision gate

**Track A — pragmatic migration with existing SQL (credit-limited, not always-free):**
- Next.js 16 App Router / shadcn / Recharts on **AWS Amplify Hosting (SSR)**, deploying *private organization GitHub repo* if account/repo integration permissions and hosting Next.js version/build compatibility are verified. Monorepo app root `apps/web`; build settings must be validated, not guessed. Do not vendor an Amplify Gen2 data/auth backend; use **Amplify Hosting only** where possible.
- FastAPI Python **3.13** API on **AWS Lambda**, adapted using `Mangum` (if compatible) or `awslabs/aws-lambda-web-adapter` with validated packaging. DO NOT use Go or deploy Stokastisk/vLLM/CUDA to Lambda. Expose an authenticated HTTPS API through **API Gateway HTTP API** (usage/cost check) or securely designed Lambda Function URL. Function URL with `NONE` is publicly callable at AWS edge regardless of CORS: application auth mandatory; prefer IAM protection where appropriate. Test actual request-body size, timeouts, Python runtime availability, async behavior and API paths.
- **Amazon RDS PostgreSQL Single-AZ** low-tier eligible configuration only if remaining Free Tier/credits and region agree; preserve existing Enigma schema, SQL atomic leases and transactions, but **Supabase-specific `auth.uid()`, RLS policies, publication/Realtime, JWT helpers and extensions do not magically work on vanilla RDS**. Rewrite or replace Supabase-specific database policies; strict server-side verified `workspace_id` authorization, least-privilege DB roles, cross-tenant tests, transactional locking and safe migrations. Do NOT execute existing `supabase/migrations` blindly on RDS; audit SQL first. Use private DB access. Lambda VPC networking, NAT Gateway costs, IPv4 charges, connection limits and RDS accessibility must be priced; no public RDS for a cheap shortcut. RDS/EC2 are not indefinitely free.
- **Amazon Cognito User Pools** replaces Clerk for an all-AWS identity plane; verify current MAU allowances, public-key JWT signature (`iss`,`aud`/client ID, `token_use`, expiry), protected group/workspace membership, frontend callback/signout and secret handling. Do not retain Clerk production dependency as hidden requirement.
- **Amazon S3** stores opt-in exports/artifacts/large immutable outputs with private bucket, block public access, encryption, presigned uploads, sizes and lifecycle; not raw model weight uploads by default. S3 storage and request charges must be counted.
- **Realtime:** start with bounded browser short-polling for job/events and an outbound Kaggle connector; avoid perpetual connections in Lambda. Keep `(job_id, event_seq)` ordering, once-only final message, job lease, idempotency and offline spool. Do not assume SQS is a drop-in replacement for atomic SQL leasing or server-push browser messages. Optional AppSync/WebSocket only after pricing, authentication and implementation review.
- **CloudFront** is normally part of Amplify Hosting delivery; do not create a redundant standalone distribution. Review bandwidth limits; built-in CloudFront allowance does not make Amplify builds/SSR free. **Route 53 hosted zones and domain registration are not permanently free**: do not create them solely because uploaded guide says 'must use'; use Amplify-provided domain for prototype, and mark requested Route53 custom DNS as deferred if $0 can't be guaranteed.
- **No EC2 by default**: an always-on `t4g.micro` EC2 + EBS + public IPv4 and possible NAT/LB/RDS has ongoing costs and ARM64 compatibility concerns. Only use as explicitly approved fallback after price/architecture tests; cannot run vLLM dual T4 on it.

**Track B — candidate for post-credit long-term cost minimization (larger refactor, not guaranteed $0):** Amplify Next.js + Lambda FastAPI + **DynamoDB** with provisioned always-free allowance + Cognito + optional S3/CloudFront, subject to current free plans and real traffic. DynamoDB has no SQL/RLS/joins; redesign workspace isolation, uniqueness, atomic job claim leases, sequence replay, retries, and schema migrations, with tests. Build a cost/complexity proof before choosing B. DO NOT swap database merely to label 'always free' without functional parity. All traffic/SSR/storage/network costs still require assessment.

**Decision rule:** Preserve RDS and SQL for lowest engineering risk ONLY if $0-out-of-pocket risk is acceptable on verified Free-plan credit-backed experiment. If account is Paid or credits not sufficient, do not claim Track A free; offer Track B local refactor and a deployment block rather than provisioning. Do not promote trial/credit eligibility into perpetual no-cost promises.

## 14.4 AWS repo/official documentation research, not code imports

Relevant inspected repositories (source/reference only):
- `https://github.com/aws-amplify/amplify-hosting` — SSR platform/hosting support, monorepo behaviors; consult Next.js supported feature list.
- `https://github.com/aws-samples/amplify-next-template` — Next.js App Router sample; **do not replace the existing Enigma code with template**.
- `https://github.com/awslabs/aws-lambda-web-adapter` and `https://github.com/aws/aws-lambda-web-adapter` — alternative FastAPI ASGI/Lambda packaging; choose one tested integration and check runtime/architecture/extension licensing.
- `https://github.com/aws/aws-sam-cli` — optional SAM deployment tooling (but do not install new global tools without need/approval).
- `https://github.com/awslabs/aws-cdk` — IaC reference; CDK uses Node/TypeScript or Python; do not duplicate infrastructure frameworks. Prefer minimal CloudFormation/SAM templates with documented no-cost validation.
- `https://github.com/aws-samples/messenger-app-amplify` — chat UI/event patterns only; not a replacement LLM backend.
- Official AWS Amplify SSR + monorepo docs, Free Tier/credit/route53/public IPv4 pricing, Lambda function URL permissions and AWS RDS pricing are authoritative over upload snapshots.

## 14.5 Existing code migration plan (incremental, no rebuild)

Phase 0. Audit `apps/web`, `apps/api`, `connector`, `packages/contracts`, `supabase`, tests, `.env.example`, `README.md`, `docs/TEST_REPORT.md`, local Git branch and current remote privacy. Enumerate actual Supabase/Clerk/Vercel touch points with file paths. `git status --short` currently has two untracked owner files: never delete/stage without review. Establish AWS account eligibility, budget and region FIRST; no write API.

Phase 1. Add interfaces/adapters **without removing working Supabase/Clerk code until AWS replacement passes tests**: `AuthProvider` with Cognito, `WorkspaceStore`, `JobStore`, `MetricsStore`, `EventTransport`, `ArtifactStore`; ensure type contracts remain stable and preserve existing mock/test fixtures. Do not add a second irrelevant ORM. Prefer a PostgreSQL implementation for RDS and a separate experimental DynamoDB option only if approved.

Phase 2. Replace Clerk-specific JWT and frontend UI/auth callback integration with Cognito; enforce application-level membership and tenant filtering on all API queries, on both web and FastAPI boundaries. Migrate test identities safely; do not assume Clerk user IDs equal Cognito `sub` values. Add full sign-up/sign-in/sign-out and dual-tenant tests, rate limits, anti-replay and CSRF protection where relevant.

Phase 3. Database migration: inspect `supabase/migrations/0001_core_schema.sql`, `0002_job_functions.sql`, `supabase/verify/rls_two_tenant.sql`, and actual store; produce portable `infra/aws/sql/` migrations and SQL verification. Separate PostgreSQL-native tables/functions from Supabase JWT/RLS policies and Realtime. Implement least-privilege DB role, pooled connections/limits, bounded retention, tested atomic leases, ordered events and cross-workspace isolation. Test local PG18 before AWS; never touch `agentic_workspace` DB.

Phase 4. Replace Supabase Realtime with bounded authenticated HTTP event polling (first implementation). Retain monotonic sequence replay, reconnect, backpressure, runtime offline display, token chunk labels, retry handling, API rate limits and final-message persistence. Use outbound HTTPS from Kaggle GPU connector, never publicly expose its localhost vLLM. Authenticate its scoped key, hash stored tokens, and cap telemetry frequency/size.

Phase 5. Adapt FastAPI to Lambda Python 3.13, safe handler, IAM permissions, secrets, narrow CORS, runtime limits. Update Next.js 16 build for AWS Amplify Hosting monorepo `apps/web`; verify SSR/server actions/middleware and production build on supported runtime. Do not install GPU wheels or CUDA on AWS API runtime. Create minimal IaC and **read-only template validation** before any provisioning.

Phase 6. With explicit approval and confirmed account plan/quota, provision only independently verified eligible resources one at a time with tags (`Project=Enigma`, `Environment=dev`, `Owner=...`), log resource IDs privately, show forecast, and define teardown. Test real AWS Cognito, database, secure API, private S3, Next.js browser, GPU relay, per-GPU NVML and real vLLM metrics. Do not claim anything deployed absent real endpoint test.

Phase 7. Run existing Python test suite, TypeScript typecheck/lint, Next.js production build, Playwright 10 mock tests, security live tests and Kaggle build-5 acceptance independently. Preserve real user data; test live on dedicated Enigma resources only. Show actual status `DONE/BLOCKED/NOT RUN` for each stage and AWS services.

Phase 8. Update `README.md`, `.env.example` (names only), `docs/AWS_ARCHITECTURE.md`, `docs/AWS_COST_MATRIX.md`, `docs/AWS_SECURITY.md`, `docs/AWS_DEPLOYMENT.md`, `docs/AWS_MIGRATION_REPORT.md`, `docs/TEST_REPORT.md`; mark Vercel notes as historical. Commit only reviewed code, docs and templates to private `stokastisk-ai/enigma` after a secrets scan. No force push, no unnecessary third-party telemetry.

## 14.6 Secure environment and deployment requirements

- Names are placeholders, never actual values in documentation or tracked files: `AWS_PROFILE`, `AWS_REGION`, `AWS_ACCOUNT_ID` (avoid committed account IDs), `ENIGMA_AUTH_PROVIDER=cognito`, `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, `COGNITO_ISSUER_URL`, `NEXT_PUBLIC_COGNITO_REGION`, `NEXT_PUBLIC_COGNITO_USER_POOL_ID`, `NEXT_PUBLIC_COGNITO_CLIENT_ID`, `ENIGMA_DB_DSN`, `ENIGMA_API_BASE_URL`, `ENIGMA_ARTIFACTS_BUCKET`, `ENIGMA_RUNTIME_TOKEN_SECRET` (only if actually used by code), `ENIGMA_ALLOWED_ORIGINS`. Browser-exposed variables must be intentional public identifiers only. Check exact names in implementation.
- Use encrypted deployment environment mechanism, AWS IAM roles, least privilege, KMS defaults as appropriate; AWS Secrets Manager is **not always free** despite being credit-eligible. SSM Parameter Store Standard may have no additional charge within limits but validate APIs/features, and understand no secrets should leak via CloudFormation outputs or logs.
- Never copy previously pasted Clerk/Gemini/database credentials; rotate secrets from earlier chat before any live integrations. Disable old providers only after AWS replacements are validated. No AWS access keys in GitHub or frontend; short-lived IAM identities. No payment/subscription upgrades without explicit approval.
- No public RDS, no NAT gateway, ALB, EC2, EKS, Fargate, ECR, AppSync, Route53 public hosted zone, paid domain, Prometheus managed workspace, or CloudWatch custom metric emission merely because services appear in a Free Tier list. Each is explicitly blocked until shown free/credit-covered and approved.
- Do not use Route53 in prototype if it incurs a recurring hosted-zone charge; document that the user's Route53 requirement conflicts with permanent $0. Expose a deployment URL only after real endpoint verification.
- Clarify cloud-hosted API does not imply cloud GPU: AWS hosts Enigma's *control plane*; Kaggle hosts user-initiated GPU inference. An entire *GPU inference platform* cannot be on AWS at $0 using dual-T4 Kaggle GPUs. Do not provision AWS GPUs.

## 14.7 Required AWS read-only CLI baseline and testing

```
cd /media/waqasm86/External1/Waqas-Projects/Project-Enigma/
command -v python3.13 && python3.13 --version
aws --version
aws configure list-profiles
aws sts get-caller-identity --query '{Arn:Arn}'
git status --short
git remote -v
git ls-files | wc -l
du -sh . apps/web/node_modules .git
python3.13 -m pytest
python3.13 scripts/smoke_e2e.py
(cd apps/web && npm run build && npm run test:e2e)
```

Never print credentials or rely on an AWS CLI `--version` to establish authorization, plan, region, balance or free eligibility. Read-only AWS service listing commands must use `--region` and `--profile` explicitly once chosen; do not call provisioning commands without owner approval and cost matrix.

## 14.8 Completion definition

Enigma on AWS is *only* DONE when the same private project (a) builds on AWS Next.js host, (b) verifies Cognito claims and two-tenant boundaries, (c) uses AWS-backed durable database with atomic jobs, (d) exposes authenticated FastAPI service, (e) receives outbound real Stokastisk build-5 model chunks/GPU0+GPU1 NVML and native vLLM Prometheus metrics, (f) shows persistent history after Kaggle disconnects, (g) meets explicitly verified eligible pricing and account restrictions, and (h) has a cleanup/termination procedure. Otherwise state precise blockers; **do not invent a live deployment**.

---

# 15. ENIGMA AWS EXECUTION POLICY — LATEST AND HIGHEST PRECEDENCE (2026-11-21)

This Section 15 is the current authoritative policy and overrides any conflicting text in Sections 1–14, historical Vercel, Supabase, Clerk and earlier AWS prompts. Do not discard historical sections or working code; mark superseded guidance as historical in documentation. Read this section **in full at every Claude Code session** through the root `CLAUDE.md` pointer. Do not claim this Markdown is auto-discovered independently.

## 15.1 Identity, scope, hardware hard exclusions

- Existing proprietary Enigma monorepo: `/media/waqasm86/External1/Waqas-Projects/Project-Enigma/`; canonical source is PRIVATE `https://github.com/stokastisk-ai/enigma.git`. Preserve company ownership, Git history and all user files. No open-source license for Enigma. Separate PUBLIC repos `stokastisk-ai/stokastisk` (SDK) and `stokastisk-ai/stokastisk-ai.github.io` (SDK docs); do not change either public repo without a separate, concrete requested SDK/docs change, independent audit, tests and authorization. The user authorizes normal reviewed Enigma private Git commits/pushes under these rules; never force push, delete history, change repo visibility or delete unrelated remote files.
- **NEVER use the laptop's NVIDIA GeForce 940M for ANY GPU processing.** No `nvidia-smi`, `pynvml.nvmlInit()`, local NVML test against physical device, CUDA probing, PyTorch CUDA, `torch.cuda`, `vllm`, NVIDIA driver changes, Nvidia container runtime, local GPU benchmark, local model loading, or local Stokastisk inference. No GPU diagnostics on this computer. GPU-related unit tests on localhost must use **fully synthetic fakes/mock fixtures** and must not initialize NVML implicitly. Device isolation is a hard requirement even for tests whose purpose is telemetry. Do not disable/modify laptop driver to comply. GPU health is **NOT tested locally**.
- GPU validation is **only** in a user-launched, authorized Kaggle notebook session with exactly two Tesla T4 / SM75 GPUs, or another explicitly authorized remote T4 host. Kaggle only for personal evaluation in accord with its terms; no 24/7 paid commercial inference promise. Real Stokastisk build-5 acceptance remains unverified until executed remotely. Do not install Stokastisk/CUDA binaries into the Ubuntu web/API Python environment or AWS Lambda.
- Use EXACT local Python executable `/usr/local/bin/python3.13` for all local Python and pip operations; verify executable path and `sys.executable` with `/usr/local/bin/python3.13 -c 'import sys; print(sys.executable)'`. `/usr/local/python3.13/bin/python3.13` may be checked for equivalence; choose `/usr/local/bin/python3.13` as canonical. Never switch to Python 3.14, `python`, `python3`, or `/usr/bin/python3` for Enigma code. AWS CLI 2's self-contained Python 3.14 runtime is NOT Enigma's Python interpreter; invoking the AWS CLI is allowed. Framework build tools may use their managed runtimes only after explicit compatibility verification. AWS Lambda runtime must specify `python3.13`.
- Local tooling: `python3.13 -m pip`, `python3.13 -m pytest`, `python3.13 -m uvicorn`, npm with existing lockfile. Do not use `uv`, `ruff`, `venv`, `virtualenv`, Poetry, Pipenv, Conda or Go. Avoid `--break-system-packages`; use pip `--user` only after checking effects and approval. Do not run `pip install` from unverified URLs. Allowed to install specific pinned, vetted pip dependencies needed for the Enigma app and tests; inspect lock/pin and `pip check` first. Do not use pip to install GPU SDK or native CUDA wheels on Ubuntu.
- Observability for GPUs: **only** `nvidia-ml-py`, `prometheus_client` and built-in Stokastisk/vLLM `/metrics`. On local Ubuntu, these modules may be statically imported for test harness only if importing has no GPU initialization, and tests mock all driver calls. AWS CloudWatch is permissible for AWS infrastructure logs with capped retention, not additional GPU instrumentation. No Grafana server, OpenTelemetry, CUDA profiler or proprietary monitoring agent.

## 15.2 Actual local state and fact verification

As of uploaded snapshot 2026-11-21 18:20 PKT: screenshot reports 788.9 MiB, 51,272 items, 47,169 files and 4,103 directories. Previous `du`: 718 MiB total, 714 MiB `apps/web/node_modules`, 1.9 MiB `.git`; earlier 157 tracked files. These are different snapshots. Fresh measurement required; never assume the GitHub repo has a 788.9 MB payload. New local inventory includes `infra/aws/sql/`, `infra/aws/template.yaml`, `apps/api/requirements-aws.txt`, `amplify.yml`, `docs/AWS_*`, and new tests. Uploaded README claims 167 pytest passing and 5 opt-in live Clerk tests skipped; this claim is not a fresh execution in this session. Use current source and tests; do not recreate the project.

Read `README.md`, full `skills.md`, `CLAUDE.md`, `.claude/skills/enigma-aws-deploy/SKILL.md`, `docs/AWS_ARCHITECTURE.md`, `docs/AWS_COST_MATRIX.md`, `docs/AWS_SECURITY.md`, `docs/AWS_DEPLOYMENT.md`, `docs/AWS_MIGRATION_REPORT.md`, `docs/TEST_REPORT.md`, `infra/aws/`, `apps/web/`, `apps/api/`, `connector/`, `packages/contracts/`, `supabase/migrations/`, tests and `.env.example`. Audit existing code, not just prose summaries. Preserve working test doubles and legacy providers until AWS equivalents demonstrably pass.

## 15.3 Git/GitHub execution authorization and hard guardrails

The owner authorizes routine reviewed source uploads to **the existing PRIVATE** `stokastisk-ai/enigma` repository after tests and secret scan; no separate prompt is necessary for each ordinary non-destructive `git commit`/`git push` of Enigma changes. Verify **authenticated** repository identity and private visibility (`gh repo view stokastisk-ai/enigma --json isPrivate,nameWithOwner,viewerPermission` when `gh` is installed/authenticated; otherwise authenticated Git API), remote URL, branch, last common commit, and remote HEAD. If connector gets 404, treat access as unverified, not proof of deletion. Never reset/reinitialize `.git` or construct a new repository. No Git history rewriting, rebase of other people's work, forced update, changing branch protection, mass deletions, or deletion of other GitHub repos. Do not push to public Stokastisk/docs repos under this general Enigma authorization.

**Before staging:** `git status --short`, `git diff --stat`, `git diff --check`, `git ls-files`, `git ls-files --others --exclude-standard`, inspect `git diff -- .gitignore`, verify `git check-ignore` on `node_modules`, `.next`, `.pytest_cache`, `.env.local`; run secret scanning across tracked *and staged* files and available history without printing values. Review actual deletion set (`git diff --name-status`) and prevent deletion of essential source. Use *explicit path* `git add` only for verified app source, SQL, IaC, docs, tests, lockfiles and instruction files; don't stage entire project indiscriminately. If a file is currently tracked but becomes unnecessary, use a normal audited `git rm --cached` only when keeping local copy is intended, or reviewed `git rm` for genuinely obsolete source with tests verifying no references. Never remove all remote files to 'replace' them; Git push only carries diffs, not node_modules. No `git clean -fdx`, `git reset --hard`, `git push --force`, `git filter-repo`, or broad `rm -rf`. Handle remote divergence by reporting/merging safely; do not overwrite unmerged changes.

Existing untracked `CLAUDE_CLI_PROMPT.md`, `README_INSTALL.md`, and new AWS instructions must be examined deliberately. `node_modules`, pip cache, `.next*`, `.pytest_cache`, Playwright artifacts, credentials, model weights, zip archives, wheel files and telemetry dumps are not source and must never be committed. Existing `supabase/` migrations are historical but important; do not blindly delete them until AWS equivalence tests pass and archival plan exists.

After the push, confirm private visibility, branch HEAD and commit SHA remotely. Report exact changed-file list, tested commands, failures, and push status. No claim of a successful upload based solely on `git status`.

## 15.4 Strict AWS cost and deployment policy

Owner wants **no AWS out-of-pocket charges**, no subscription, no paid addons, and AWS hosting instead of Vercel. Prior user AWS diagnostic reported a PAID account plan with a remaining promotional-credit balance; this is a historical snapshot, not live account data. AWS credits can expire; Free Tier eligibility depends on date, region, plan, service and aggregate consumption. AWS Free plan can expire after 6 months or credit exhaustion, while Paid plans can bill beyond allowances. Budget notifications are **not hard caps**. **Never create any billed resource or deploy without verifying current billing plan, credits, expiry, eligibility and exact downstream charges, and presenting the resource-specific residual risk for the user's explicit approval.** General permission to work on Enigma/GitHub and this instruction file is NOT permission to incur billing or provision chargeable AWS resources. If true zero-charge deployment cannot be demonstrated, stop provisioning and complete local migration, read-only checks, Git push and blocking report.

Read-only AWS checks with installed CLI (`aws --version`), existing profile/role and region; do not create new IAM keys. Do NOT print access keys, tokens, full account number or secrets. Inspect Billing/Free Tier and Cost Explorer/credits through read-only API or user dashboard as available, and cite pricing pages in `docs/AWS_COST_MATRIX.md`. AWS CLI version alone proves nothing about AWS authorization. Region must be explicit for queries. Evaluate all cost components: Amplify SSR/build/egress, Lambda requests/duration/logs, Cognito MAU/tiers, PostgreSQL RDS instance/storage/backups, database networking/ENI/NAT/IPv4/egress, API Gateway or Lambda URLs, S3 requests/storage, Route53 zones/domain, CloudFront, CloudWatch, and any secrets manager/SSM fee. Avoid NAT gateways, public RDS/IPv4, EC2, Fargate/ECS/EKS, ALB, managed Prometheus and custom domains by default; avoid CloudFormation stacks until approved. Set log-retention and storage quotas before any launch. Tag planned resources and prepare a precise teardown script.

**2026-11-21 compatibility alert:** Official AWS Amplify Next.js deployment guide currently explicitly documents SSR support for Next.js **12–15**, but Enigma uses **Next.js 16**. Do not silently downgrade Next.js or claim SSR compatibility; do an official documentation check and a supported non-billed build/package validation first. A real deploy requires compatibility proof; if it cannot be proven, report BLOCKED or propose a tested supported architecture, preserving UX and Next.js 16 wherever practical. Next.js 16 may require investigation of other adapters and cost implications; no silent rewrite.

**Recommended staged architecture, not an assumption of free runtime:** AWS Amplify Hosting **if** Next 16 compatibility is verified, Python 3.13 FastAPI AWS Lambda via tested Mangum or Lambda Web Adapter, Cognito user pools, and existing PostgreSQL semantic behavior preserved on RDS **only if** affordable under verified account credits and approved. Secure authenticated inbound API and outbound polling from Kaggle; browser bounded polling instead of Supabase realtime initially. AWS Cognito `sub` IDs do not equal Clerk IDs; enforce tenant membership everywhere. Supabase RLS/auth helpers and Realtime do not run unchanged on plain PostgreSQL. DynamoDB is an optional separately tested redesign with conditional leases and tenant-index policy; never claim it is drop-in. Lambda Function URL `NONE` + CORS is not authentication. Consider route cost and actual authentication. Maintain exact job/streaming idempotency, ordered event replay, bounded metrics, retention and two-tenant isolation.

**Zero-spend contradiction:** The complete commercial AWS hosting stack cannot be guaranteed permanently free at arbitrary usage, particularly SSR, PostgreSQL, public network and domain services. Always give a truthful blocked/conditional deployment status instead of promising zero dollars. Kaggle dual T4 is not AWS GPU capacity and cannot be sold as permanently available. Real AWS endpoint + real remote T4 acceptance are separate gates.

## 15.5 AWS work plan and acceptance gates

A. Inspect full local source, current AWS migration artifacts and Git; produce gap matrix and dependency graph. Verify local Python path first. Explicitly disable local GPU probe paths in tests and smoke commands; replace any physical NVML or `nvidia-smi` calls with mocks. Do **not** execute unsafe existing scripts until audited.
B. Run unit/integration/SQL/browser tests using only CPU and synthetic GPU telemetry; compare to historical 167 pytest passed / five skipped / ten mock browser reported results. Run `npm run build`, typecheck, lint, applicable Playwright, dependency audit; log what truly ran. No local NVIDIA/torch CUDA calls.
C. Integrate AWS Cognito, FastAPI Lambda Python 3.13 handler, verified Next.js deployment strategy, storage adapters (portable PostgreSQL functions and/or explicitly approved DynamoDB alternative), bounded HTTP polling, cloud authentication and no false metrics. Keep legacy providers behind opt-in flags until equivalence confirmed. Avoid refactoring SDK binary.
D. Run AWS read-only account, quotas, pricing and IaC syntax checks; generate `docs/AWS_COST_MATRIX.md` with status `VERIFIED_FREE`/`CREDIT_ONLY`/`UNKNOWN`/`BLOCKED`, test deployment plan and teardown. Never interpret a successful CloudFormation template validation as proof of free cost or real deployment.
E. If cost/compatibility checks **and** explicit deployment approval are present, provision narrowly and validate AWS actual endpoints for auth, tenant isolation, durable jobs, safety, browser and connector. Otherwise stop **only provisioning**, not local coding/testing/Git workflow.
F. Separately provide an updated remote Kaggle build-5 notebook/checklist, **without executing it on laptop**. Confirm 2 Tesla T4 IDs, SM75, model, `/health`, `/metrics`, streamed completions, per-GPU telemetry and offline recovery in remote session. Do not claim done without actual Kaggle evidence.
G. Document results: `README.md`, `docs/AWS_*`, `docs/TEST_REPORT.md`, `docs/SDK_INTEGRATION.md` and `BLOCKED_EXTERNAL_SETUP.md`; mark Vercel history. Keep a clear status for local changes, Git remote push, AWS account check, AWS deployment, Cognito, DB, Kaggle and end-to-end.

## 15.6 Lean project + deletion safety

Snapshot screenshot 788.9 MiB is **local directory**, not GitHub repository size. Most bytes came from npm dependencies previously; measure again, do not assume. Run `du -sh . .git apps/web/node_modules apps/web/.next 2>/dev/null`, `git ls-files | wc -l`, and list large non-node_modules files. Do not delete `node_modules` during active builds without specific authorization and validated `package-lock.json`. May remove known generated `.next`, `.next-e2e`, `.pytest_cache`, `test-results` after ensuring no active process needs them, documenting exact paths. Do not delete unknown untracked files, user drafts or Git history. Use `.gitignore` rather than deletion as primary upload-size control. Do not store downloaded GitHub reference repos or large model artifacts in Enigma.

## 15.7 Final report contract

For each run provide: (1) exact local Python interpreter and no-local-GPU assurance; (2) real baseline vs updated test counts; (3) exact Git files created/modified/deleted and remote visibility/commit verification; (4) AWS resources **created** (usually NONE until approved), region, price and limits, endpoint status; (5) Kaggle real GPU status (NOT RUN unless remotely evidenced); (6) unresolved blockers; (7) safe disk cleanups and what was preserved. Do not claim full AWS deployment if only adapters/IaC are implemented.


---

# 16. GPU COMPUTE POLICY — KAGGLE DUAL-T4 ONLY (LATEST AND HIGHEST PRECEDENCE)

Effective 2026-11-21. **Section 16 overrides any contradictory instruction in Sections 1–15 or any earlier Claude prompt.** All prior sections remain for historical context, but their mentions of AWS GPU, optional alternate GPU hosts, local 940M tests, or automatic GPU provision are superseded. Claude must read this section on every session through `CLAUDE.md`.

## 16.1 Strict compute boundary

**The only authorized GPU inference environment, for now, is a user-started Kaggle notebook with precisely 2 × NVIDIA Tesla T4 GPUs (SM75), running the released Stokastisk 0.1.0 build-5 Python SDK and its built-in vLLM 0.25.1, CUDA 13 SM75 binaries and TRITON_ATTN backend.** No alternate GPU host is automatically authorized; a future host requires a distinct explicit owner decision and changed instructions.

- **FORBIDDEN on AWS:** EC2 GPU instance families (including P, G, DL, Trn, Inf and any accelerator-bearing instances); EC2 NVIDIA GPU instances; SageMaker GPU endpoints/training; EKS/ECS/Fargate GPU nodes; AWS Batch GPU; AWS Bedrock, GPU inference as a service, GPU marketplace or rented third-party compute; NVIDIA GPU AMIs, CUDA devices, models/weights on AWS for inference; provisioning any GPU resource, even if credits may pay for it. Do not suggest these as default alternatives or create cost estimates that imply approval. The user wants no AWS GPU spending at all, not even with credits. An AWS CPU-only Lambda / non-GPU compute component is allowed only for web/API/control-plane tasks, subject to cost approval.
- **FORBIDDEN on local Ubuntu:** running/probing the GeForce 940M, `nvidia-smi`, initializing NVML, `torch.cuda`, CUDA/vLLM inference, GPU driver or toolkit operations, physical GPU benchmark. All local telemetry tests must use mocks and must be audited before execution. Never install or rebuild Stokastisk GPU wheel/CUDA dependencies on this laptop. Use `/usr/local/bin/python3.13` for every local Python command, including pip and pytest. No `uv`, `ruff`, `venv`, Poetry, Conda or Go.
- **ALLOWED Kaggle only after user launch/terms verification:** verify two genuine T4s (not merely mock device names), Python compatibility, SHA256-pinned SDK build-5 installation, Qwen2.5-0.5B TP=1 initial serving, `/health`, `/v1/chat/completions`, native `/metrics`, `nvidia-ml-py` per-device measures, `prometheus_client` metric parsing, emitted chunks, shutdown, and disconnect. Test larger models/TP=2 separately and label unverified results. Never promise persistent GPU uptime or use Kaggle free compute as a paid commercial production backend without explicit provider permission.

## 16.2 Allowed AWS role: CONTROL PLANE ONLY

AWS may host (only after explicit, itemized billing approval) Enigma's Next.js 16/shadcn/Recharts UI, FastAPI Python 3.13 control API, Cognito identity, durable jobs/events/metrics, data storage, static asset delivery and operational logs. **AWS must never generate LLM tokens**. The complete live data flow is:

1. Signed-in browser submits prompt to authenticated AWS control-plane API; backend verifies user, tenant and quotas, persists a bounded inference job.
2. Kaggle-hosted Stokastisk connector **initiates outbound HTTPS** to claim jobs with scoped, revocable runtime authentication; no inbound Kaggle port, no public vLLM server, no tunnel required.
3. Kaggle vLLM generates model responses; connector posts ordered `event_seq` response chunks and measured GPU0/GPU1 + vLLM metrics. The browser receives incrementally through authenticated bounded polling (or a separately cost-approved transport).
4. AWS persists final assistant message ONCE and historical telemetry; after Kaggle closes, runtime is OFFLINE and chat requests are not silently rerouted to AWS GPUs or paid hosted models.
5. Maintain idempotent claims, expiring leases, replay/resume, retries, cancellation, bounded local spool, encryption, event retention, rate limits, upload budgets and cross-tenant authorization.

Do not introduce AWS Bedrock, proprietary LLM API, Gemini or OpenAI API as a hidden chat fallback. Do not place Stokastisk inference artifacts in an AWS Lambda ZIP/image or install `torch`, `vllm`, CUDA wheels, or `pynvml` there. The AWS side may **store** measurements but GPU observability instrumentation is **only** `nvidia-ml-py`, `prometheus_client` and built-in vLLM metrics on Kaggle.

## 16.3 Latest reported project checkpoint — verify, never assume

From owner's latest Claude CLI transcript (not independently re-executed in this instruction file): latest private Enigma `main` commit `fd3f759`, following `4916f44`, reportedly synchronized; 184 Python tests passed, 6 skipped; smoke succeeded; SQL suites 61 and 78 assertions; 15 Node unit tests; 10 browser tests; lint/typecheck clean. No AWS resources changed, real AWS/Kaggle integration NOT tested. The source of truth is the current Git remote and fresh test results, not this checkpoint. Normal reviewed non-destructive pushes to `stokastisk-ai/enigma` are authorized; preserve private visibility, no force pushes, never modify public SDK/docs repos under this authorization. Untracked owner files must not be deleted, staged or overwritten without inspection.

The AWS CLI reportedly uses **ROOT access keys**, with MFA enabled. This is a serious security issue: do not use root for deployment/automation, do not create or rotate keys in unattended code, and do not remove existing credentials until a tested non-root admin/deployer access path is established and owner explicitly authorizes the security change. Prepare a least-privilege IAM role / IAM Identity Center plan and owner-facing key-rotation checklist. Read-only account investigation should prefer a non-root profile; if only root access exists, stop cloud operations requiring it and report. Never output credential values or account IDs into project Git, logs or chat.

The AWS account was reported PAID with $99.99 promotional credits; exact credit expiry and Free Tier allowance applicability remain UNVERIFIED. **No pricing model can guarantee $0 spending**. Latest Claude's stated *unverified* `us-east-1` monthly scenarios: RDS Track A $14.21 / $15.51 / $19.48 and DynamoDB Track B $0.37 / $2.40 / $7.44 (low/moderate/high). These are assumptions, not invoices or authorization. No provisioning or AWS billable changes without account-specific plan/credit expiry, region, exact resource list, itemized dependency costs, cleanup plan, explicit user approval and defined ceiling. An alert is not a hard spend cap.

## 16.4 Development decisions and cost controls

- **Priority: build and test Track B DynamoDB parity offline** to reduce fixed RDS costs while retaining the existing PostgreSQL implementation until parity is proven. Allowed to install *only vetted pinned* development dependencies (`boto3`, `moto` or DynamoDB Local if genuinely needed) with `/usr/local/bin/python3.13 -m pip`, but DynamoDB Local is a Java application, not a pip package; verify provenance, size and cleanup before any download. No AWS resource writes for local tests. Prove tenant isolation, strong/conditional atomic claims, lease expiry, ordered event replay, deduplication/idempotency, quotas, retention and cancellation with failure injection. Do not announce migration as complete based only on unit tests.
- CloudFront vs direct Function URL, 1-second vs 5-second idle connector polling, Lambda memory/duration, CloudWatch log retention and Cognito cost must be backed by actual traffic calculations. The reported 5-second idle interval may reduce traffic but adds pickup latency; make configurable with fast poll while an active prompt awaits, jitter/backoff when idle and bounded API use. Require test measurements before publishing latency claims.
- Next.js 16 web tier: evaluate Lambda Web Adapter for Node.js (ARM64 only after confirming native dependency compatibility), CloudFront host/origin handling, redirects, cookies, caching, SSR/static routes and streaming; no silent Next.js 15 downgrade. AWS Lambda Web Functions is separate from Python FastAPI and may have different limits. Check official docs and live tests when deployment is approved.
- Authentication/security: audit `COGNITO_JWKS_JSON` pinning against key rotation/revocation and fail-closed behavior. Do not bake long-lived JWKS into publicly accessible Next.js code. Prefer normal cached JWKS retrieval from permitted network locations or a documented safely maintained rotation process; assess VPC/NAT costs before choosing. Test CloudFront host-header forwarding and allowed public origin; never trust arbitrary forwarded headers.
- Keep only necessary source and IaC, no copied GPU dependencies, no 788MB `node_modules` upload. Preserve `.git`, source, lockfiles, migrations, tests, docs, pending user files and all Git history. Clean only positively identified generated artifacts after reviewed inspection. Never indiscriminately delete remote repository files; push reviewed diffs after secret scan.

## 16.5 Required work in each Claude session

1. Read `CLAUDE.md`, full `skills.md` including Section 16, skill and new prompt, current git status, AWS migration docs, source and tests. Confirm `/usr/local/bin/python3.13` path, root/non-root AWS identity and **no local GPU access**.
2. Audit current code for forbidden AWS/local GPU pathways, GPU EC2/SageMaker/Bedrock provision references, GPU dependencies in Lambda packages and hidden fallback model APIs. Remove proposed GPU infrastructure from plans/IaC only after inspection; preserve historical evidence files.
3. Run safe CPU-only tests and actual Next.js lint/typecheck/build/browser tests; label AWS-live and remote Kaggle checks NOT RUN if so. Keep the 184-pass/6-skip count as prior report, not a promise.
4. Complete DynamoDB adapter parity tests locally, tighten AWS web/API routing, develop explicit resource inventory + cost scenarios with sourced official pricing and a zero-spend assessment. Do not deploy before explicit authorization.
5. After secret scan, push only reviewed needed Enigma source/docs/tests/IaC to existing private repo; verify remote private and SHA. Do not change Stokastisk or docs repos.
6. Prepare authorized Kaggle notebook with remotely executable checks; do not run it locally. Final report: exact AWS resources created (normally none), Git commit SHA, code tests, AWS plan/credit unknowns, GPU location Kaggle ONLY, root-key remediation, remaining blockers, and whether real E2E has been proven.

**Completion criterion:** Real-time chat + measured observability must be tested with a user-started Kaggle 2×T4 session and the verified AWS control plane, and **no AWS GPU of any kind**. Until then, `AWS_DEPLOYED` and `KAGGLE_E2E` statuses remain BLOCKED or NOT RUN, never DONE by inference.


---

# 17. Enigma AWS service visibility dashboard and completion charter — AUTHORITATIVE OVERRIDE (2026-11-21)

**Precedence:** Section 17 supersedes conflicting directions in Sections 1–16. Keep all historical sections for context; edit only relevant contradictions. This is an extension of EXISTING Enigma, not a request to rebuild it. Source of truth is inspected code, fresh tests, AWS console and Git remote. Claude must re-read this charter each session through `CLAUDE.md`.

## 17.1 Latest reported checkpoint (verify, not assume)

Owner's latest Claude transcript states private Enigma `main` commit **56c0739**, 38 files modified/added (9 new, 29 changed, none deleted), **317 Python tests passed, 6 skipped**, smoke passed, SQL checks 61 and 78, 15 Node unit tests, 10 browser tests, and no reported npm production vulnerabilities. DynamoDB Track B passes moto/emulator parity including eight-thread contention, ordered events, idempotency/caps/retention; it has **NOT** passed against real AWS DynamoDB. AWS resources NOT created. Cloud rollout, Lambda Web Adapter + Next.js 16, Cognito deployment, and actual Kaggle build-5 end-to-end test NOT RUN. Do not upgrade status without live evidence. Prior cost estimates for Track A and B are indicative, not quotes. Root-user AWS CLI keys reportedly exist; this is a STOP condition for deployment writes.

## 17.2 Immutable GPU and Python boundaries

- AWS is **control plane only**: Next.js 16 frontend, FastAPI Python 3.13 backend, Cognito user identities, DynamoDB storage, low-cost event/status polling, AWS operations and cost visibility. NO AWS EC2 GPU, no EC2 CPU VM by default, no AWS Bedrock, SageMaker, GPU AWS Batch/EKS/ECS, paid inference provider or CUDA/vLLM in Lambda.
- **Only a user-initiated Kaggle session with two NVIDIA Tesla T4 GPUs** may execute Stokastisk 0.1.0 build 5, vLLM 0.25.1 and TRT/Triton CUDA binaries, subject to provider terms; no 24/7 or paid inference promises. No model traffic to third-party hosted inference.
- Local NVIDIA GeForce 940M MUST NOT be accessed, enumerated or probed. No `nvidia-smi`, local NVML initialization, torch.cuda, CUDA library import, local vLLM runtime or GPU benchmarks. GPU tests must be mocked on local machine; actual T4 checks only in the user-initiated Kaggle notebook.
- Use exactly `/usr/local/bin/python3.13` for EVERY local Python invocation and `/usr/local/bin/python3.13 -m pip` for installs. No uv, ruff, virtualenv/venv, Poetry, Pipenv, Conda or Go. No `--break-system-packages` without express authorization. Verify executable exists before work.
- GPU inference observability instrumentation is LIMITED to `nvidia-ml-py`, `prometheus_client`, and vLLM's built-in metrics running on Kaggle; AWS CloudWatch is used solely for **AWS operational metrics** (Lambda duration/error, DynamoDB throttling, CloudFront traffic, Cognito, billing where supported), not as a substitute GPU telemetry collector. Preserve metrics ingestion and persisted history in Enigma.

## 17.3 Deliver TWO separate but complementary AWS visibility experiences

**(A) Native AWS CloudWatch dashboard — Enigma-Control-Plane:** Create a source-controlled, reviewable dashboard JSON/IaC draft under `infra/aws/dashboards/enigma-control-plane.json` or an existing IaC conventions-compatible location. Prefer ONE native CloudWatch dashboard with <=50 existing AWS-provided metrics and remain within the account's combined 3-dashboard free allowance IF independently confirmed. Never create arbitrary custom metrics or detailed monitoring without checking charges. No default Container Insights, CloudWatch Application Signals, X-Ray tracing, Logs Insights scheduled queries, metric streams, CloudWatch Synthetics canaries, RUM, third-party agents, or cross-account observability. Include only deployed, existing resources; placeholders in drafts must never imply live resources. Group widgets: web Lambda requests/errors/duration/throttles/concurrency; API Lambda invocations/errors/duration/throttles; DynamoDB consumed RCUs/WCUs, throttled requests, system errors; CloudFront requests/4xx/5xx/bytes; optional Cognito sign-in or S3 requests ONLY where supported without new charge. Restrict AWS log retention to reviewed short period (e.g. 7 days) and redact prompts/tokens. Dashboard is a *draft* until read-only resource verification and explicit approval for the AWS write `PutDashboard`. Verify CloudWatch monthly free-tier quota and existing account dashboards first. If dashboards would incur costs, STOP and use existing AWS console views.

**(B) Enigma-admin-only AWS Infrastructure tab:** Extend the existing Next.js/shadcn UI with `/settings/aws` or `/admin/aws` and a clear `AWS Infrastructure` navigation item. This tab is separate from GPU inference dashboard and accessible only to the verified Enigma owner/admin, with default-deny authorization and direct-route API authorization tests. Show actual AWS services deployed for Enigma, their region, service/resource type, resource status, last verified timestamp, host health, deployment build/commit hash, Lambda invocations/errors/duration, DynamoDB throughput/throttling, CloudFront request/error activity, Cognito users if permissible, and current cost/credit/free-tier **only when retrievable securely and permitted**. Provide explicit NOT DEPLOYED, UNKNOWN, NOT AUTHORIZED, NO DATA and STALE states. Never fake an existing resource, zero bill, or AWS account credit expiry. Do not display IAM account IDs, ARNs, access keys, secret values or raw user data to end users. Infrastructure widget data should be bounded and refreshed on demand or via low-frequency TTL caches, never 1-sec AWS API polling.

- Backend uses **Python 3.13 FastAPI + boto3** (already planned for DynamoDB) with AWS role-based credentials and least-privilege read-only permissions. Never use root keys, browser-side AWS SDK credentials, or raw AWS credential values. For inventory, prefer safe validated stack outputs or a tiny explicit manifest/allowlist of AWS resource IDs with read-only service `Describe*`/`List*` calls, optionally Resource Explorer after checking indexing and dependencies. For monitoring, use CloudWatch `GetMetricData` with bounded metric identifiers and fixed time ranges. Do not assume every metric is emitted by every service. Cost Explorer's **console UI is free**, but API calls/pricing and IAM scope must be verified before using backend API; default cost panel to link out to AWS Billing/Cost Explorer instead of automated requests. No automatic AWS `Create*`, `Update*`, `Put*` or `Delete*` from the Enigma dashboard.
- A static architecture topology/health panel and list is enough for MVP; no paid map/topology dependencies. Project/Environment/Owner tags may be used for attribution after approval. If tagging resources incurs or depends on resource writes, do not do it without explicit authorization.
- The in-app AWS dashboard must NOT query GPU utilization from AWS: Kaggle GPU telemetry remains a distinct Enigma page backed by `nvidia-ml-py` and built-in vLLM metrics. AWS infra metrics and Kaggle telemetry have separate labels, sources, permissions and clocks.
- Prefer canonical shadcn/ui components and existing Recharts, no Grafana deployment, Go, commercial visualization SDK or separate new database. All new UI should meet keyboard accessibility, light/dark contrast and responsive layouts.

## 17.4 Root credentials and zero-out-of-pocket security gate

- User says AWS is already logged in and offers root access. **NEVER ask for root password, root access keys, OTP or MFA codes and never use root for deployment.** If `aws sts get-caller-identity` indicates root, STOP all account-changing actions. The owner must personally set up an IAM Identity Center/least-privilege role or equivalent non-root identity, test it, and only then revoke root access keys following AWS official guidance. Claude may prepare read-only review documentation/policies; it must not destroy root keys or lock out the owner. If login involves root, do not automatically reuse it or write AWS config credentials.
- Account reported PAID with $99.99 credits; expiration unknown; account applicability of Always Free allowances UNKNOWN. A Budget alarm is NOT a spending cap. Determine credit expiration from Billing console (owner interaction) and compare Free Tier by region; do not infer exact expiry from account creation date.
- `us-east-1` was used for prior estimates; it is NOT owner approval of region. Produce an itemized resource manifest with projected low/moderate/high cost and free-tier/credit assumptions. No AWS infrastructure creation, dashboard `PutDashboard`, DynamoDB table create/delete, Cognito creation, Lambda creation, CloudFront changes, CloudFormation deployment, IAM mutation or S3 creation until verified non-root identity, approved region, approved exact manifest, an explicit dollar-risk ceiling, and explicit permission to execute that specific AWS write.
- AWS dashboards may be within Free Tier but are NOT universally guaranteed $0, especially if 3 existing dashboards already consume allowance or metric count exceeds 50. CloudWatch Logs, metric retrieval API, storage, DynamoDB, Lambda, network, Cost Explorer API, CloudFront all have limits/charges. Keep a feature-by-feature approved cost ledger and rollback plan.
- Enigma is proprietary and private at `https://github.com/stokastisk-ai/enigma.git`. Authorized: make local source/doc/tests/manifest edits and normal reviewed, secret-scanned pushes to PRIVATE repo. Forbidden: making public, deleting remote files without review, force push, history rewrite, touching public SDK/docs repos, adding credentials or large artifacts to Git.

## 17.5 Remaining engineering tasks from latest CLI report

1. **DynamoDB real-table parity:** existing moto success is NOT AWS acceptance. Keep PostgreSQL untouched. Draft a throwaway-table test with provisioned Standard class within verified account allowance (include indexes and related capacity). Add automatic test teardown under the same reviewed deployment plan. Once approved non-root identity + region + cost ceiling + explicit table approval exist, run real DynamoDB tests: concurrent conditional job claim, ordered events, duplicate submit/idempotency, tenant isolation, pagination, throttling/retries, TTL behavior/lag, retention and error handling. No provisioning before gate. Do not assert TTL deletion is immediate.
2. **Lambda Next.js 16 integration:** build and test standalone output + AWS Lambda Web Adapter locally using safe CPU-only mock mode (no local GPU); check Node runtime/ARM64 dependency compatibility, CloudFront Host/Origin, cookies/redirects, SSR, route handlers, streaming response behavior and IAM auth. Keep Python FastAPI in a separate Lambda runtime (3.13), no GPU imports. If online-only compatibility tests require actual deployment, mark NOT RUN until approved.
3. **Cognito and JWKS:** verify JWT issuer/audience/token_use, strict tenant membership, key rotation and cache refresh, CSRF/Host spoof defense, sign-up/sign-out/callbacks. Do not embed Cognito private keys; JWKS contains public keys. Preserve fail-closed behavior.
4. **Networking and streaming:** test signed-in browser -> AWS durable job -> outbound Kaggle connector -> ordered model-generated chunks -> browser -> persistent history; test offline/reconnect/cancel/replay and mark mock vs real. Do not use cloud GPU/model fallback. User launches Kaggle notebook; Claude can prepare validation scripts and instructions, not claim a real run.
5. **Observability UI:** implement AWS native dashboard template and Enigma admin infrastructure page described in 17.3; add frontend API, authorization, data provenance, resource health, empty state, unit + browser tests. Avoid cost-triggering polling and custom metrics.
6. **Cost optimization:** evaluate whether low-rate idle polling helps real workload without unacceptable chat latency, retain as an opt-in setting. Investigate CloudFront's incremental value vs raw Function URL, but keep verified security/cookie correctness; no blind optimization or invented savings.
7. **Git housekeeping:** inspect 2 historical untracked files `CLAUDE_CLI_PROMPT.md`, `README_INSTALL.md`; preserve unless user explicitly authorizes removal. Preserve `node_modules`, lockfile, tests, migrations, docs and `.git`; exclude dependencies from Git. Review full tracked file list before cleanup.

## 17.6 Operational dashboard acceptance tests

- Default admin-only; anonymous and nonadmin users receive 401/403, and direct backend API calls enforce the same rule. Test two users and no cross-tenant inventory leakage.
- Inventory: zero-resource account/region shows **No Enigma AWS resources deployed**, not graphs fabricated from mocks; source/region/time labelled. Mock demo data must explicitly say SYNTHETIC.
- CloudWatch dashboard JSON validates locally, metric selectors use genuine AWS namespaces and metric names; automatically checks <=50 metrics and existing dashboard free-tier availability prior to any future `PutDashboard`.
- API metric queries use bounded time ranges, pagination, timeouts, retries, TTL caching and deny unknown resource IDs; cost responses never expose bills to unprivileged users.
- Dashboard does not create AWS resources, hit billing APIs repeatedly, send AWS costs to public clients, or initialize local GPU. No use of root credentials.
- Run `/usr/local/bin/python3.13 -m pytest`, existing SQL, mock smoke, TypeScript lint/typecheck, node unit, browser E2E, security scans. Record exact fresh results, including six earlier skipped and whether they remain skipped.
- Only after authorized AWS rollout: independent deployed endpoint tests, actual metrics/links, resource inventory verification, then Kaggle build-5 dual T4 acceptance and end-to-end browser/Kaggle test. No claim of completion without that evidence.

## 17.7 Required docs and checkpoints

Update `README.md`, `.env.example` with NAMES ONLY, `CLAUDE.md`, `docs/AWS_ARCHITECTURE.md`, `docs/AWS_COST_MATRIX.md`, `docs/AWS_DEPLOYMENT.md`, `docs/AWS_SECURITY.md`, `docs/AWS_MIGRATION_REPORT.md`, `docs/TEST_REPORT.md`, and new `docs/AWS_OPERATIONS_DASHBOARD.md` with UI screenshots from mock as such, real deployment only when verified. Use the AWS service list text uploads as *reference lists*, not permissions to provision everything: the earlier `Must-Use-AWS-Fundamental-Services` proposes EC2 t4g.micro/RDS/Route53, which conflict with the current no-EC2/no-fixed-spend design; explain these differences rather than implementing them. Always Free catalog lists CloudWatch dashboard allowance but also includes potentially paid features — verify official price/current quotas.

Report final statuses individually: PRIVATE GITHUB SHA, nonroot identity, root-key remediation, credit expiry, AWS free-tier eligibility, DynamoDB moto, real-DynamoDB, Lambda Next.js packaging, real Lambda/CloudFront/Cognito, CloudWatch dashboard draft/live, Enigma AWS tab mock/live, Kaggle build5, browser→AWS→Kaggle E2E. Explicitly list AWS resources created/changed and costs if any. If no approval/no nonroot role, stop before ANY AWS write and deliver local code/docs/test results + a precise owner-facing blocker/approval checklist.


## 17.8 Lessons recorded 2026-11-21 (implementation notes, subordinate to 17.1-17.7)
- **Visibility built, deployment gated:** CloudWatch dashboard draft (30 AWS-native metrics, validated offline, no custom metrics) and an admin-only page with a read-only boto3 reader (six calls, manifest ids only, TTL cache, scrubbed output). Neither is deployed; the page's default is "No Enigma AWS resources deployed". Cost/credits are link-out only.
- **Write tests that can fail:** a screenshot found four UI defects no test had asserted (duplicate "ago", uptime shown as an age, a misleading linear topology, "present" for synthetic rows); a new pagination test found a **silent truncation bug** in `_batch_get`; a new Cognito test found that an **empty `sub` was accepted**; a local CloudFront-style proxy found missing `Cache-Control: no-store` on auth responses. Prefer a test that exercises the failure over a count of passes.
- **A 5 s idle poll and CloudFront are not cost levers** (both save under a few dollars a month); decide on latency, simplicity and security. At list price a raw Function URL is cheaper than CloudFront.
- **Package for either Lambda architecture:** disabling image optimisation and excluding `sharp` removed the only native binary (44 MB → 22.8 MB).
- **Authorization order matters:** the admin API checks identity and admin status *before* parameter validation so a non-admin cannot probe it.
- **Real-DynamoDB parity** has a guarded harness (throwaway `enigma-parity-*` table, non-root, approved region, explicit opt-in; every refusal unit-tested) and a measured cost (< $0.01 of capacity); the risk is forgetting to delete the table, not the bill. Still NOT RUN.

---

# 18. AWS Identity Center account-assignment recovery and safe metadata retention — CURRENT AUTHORITATIVE OVERRIDE (2026-11-22)

**Precedence:** Section 18 supersedes contradictions in prior sections, Claude prompts, and historical Vercel/Clerk/Supabase plans. It supplements, rather than weakens, Section 17's absolute local-GPU exclusion, Kaggle-only inference, non-root AWS identity, private Git and cost-approval rules. Claude Code must load the full file through `CLAUDE.md`; root `skills.md` is not automatically discovered as a skill.

## 18.1 Confirmed current problems

- The project's `README.md` was overwritten with `# Enigma AWS assisted setup — Python 3.13` instead of the application overview; `git diff -- README.md` documents the change. Before repairing, preserve the bootstrap README as `docs/AWS_BOOTSTRAP_README.md` and verify no owner changes would be lost. Restore the application README from version control and update it only with verified current AWS status. Do not overwrite README_INSTALL.md or other unknown untracked files.
- `aws sso login --sso-session p1-engima` succeeded for the AWS Identity Center access portal. The name is intentionally spelled `p1-engima` in the existing configuration; do not rename it casually. `aws configure list-profiles` shows `default` and `vllm-temp` only; `~/.aws/config` contains `[sso-session p1-engima]` and `[profile vllm-temp]` but no `[profile enigma-deployer]`. Therefore `aws sts get-caller-identity --profile enigma-deployer` cannot work yet.
- `aws configure sso --profile enigma-deployer` produced **`No AWS accounts are available to you.`** This is **NOT** fixed by inventing an SSO name or manually adding an unassigned `sso_role_name`. It means the signed-in SSO principal has no available AWS account assignments through that instance, or there is a mismatch involving Identity Center instance type, identity, or assignments. An AWS account/permission set must be granted by an authorized administrator and the owner must choose the correct role; browser SSO login is authentication, not resource authorization.
- An **IAM Identity Center organization instance** supports AWS account access through permission sets and account assignments. An account instance cannot simply be used as if it were an organization instance. Do NOT automatically create an AWS Organization or enable/configure it with root via CLI. Have the owner inspect the current instance type and, if necessary, officially supported alternatives.
- The previously used AWS CLI default credentials were reported to authenticate as ROOT. Never use them for Cloud account operations while doing this recovery. Do not ask for or copy access keys, MFA codes, passwords, session tokens, cookies, credentials files, or SSO caches. Avoid printing complete account IDs or access portal URLs into Git, chat, test logs or reports.

## 18.2 Automatic discovery, no repeated entry of nonsecret settings

- Use the **stdlib-only, read-only** utility `scripts/enigma_aws_discovery.py` with `/usr/local/bin/python3.13`. It inspects `~/.aws/config` **only**, never `~/.aws/credentials`, cache tokens or secret-valued environment variables. Offline is the default and makes **no AWS calls**. It identifies configured SSO session names, whether the access portal and region are configured, which CLI profiles exist and whether a named role/account mapping exists, and basic current Git status.
- It creates `.enigma-local/aws-discovery-private.json` and `.enigma-local/AWS_DISCOVERY_STATUS.md` with restricted filesystem permissions, only on the owner's Ubuntu computer. Ignore `/.enigma-local/` in `.gitignore`; never commit, upload, paste or share these files unless owner separately scrubs and approves. Avoid storing account identifiers in committed `skills.md`, `CLAUDE.md`, example config, public web pages or GitHub issues. Nonsecret names and regions can be re-used safely without manual repetition.
- The optional `--online` mode is permissible **only after a named SSO profile exists and is configured**. It runs **only** `aws sts get-caller-identity --profile enigma-deployer` (read-only), with AWS credential environment overrides disabled; compares the STS result to the profile's account and verifies an `assumed-role` ARN without printing the ARN or account ID. It makes no IAM, SSO administration, billing or resource-listing calls and no mutations. Do not use the `default` profile for AWS API calls.
- The exact promotional credit expiration date is **not proven by an SSO login**; owner reads AWS Billing → Credits and may store the date manually with `--credit-expiry YYYY-MM-DD`. This date is owner-supplied, not independently API-verified. Never fabricate it. Any dollar ceiling saved locally is a *recorded decision*, not a hard AWS spend cap. A deployment needs new owner approval of an exact itemized manifest, region, risk ceiling and teardown.
- If `No AWS accounts are available` persists, Claude must stop cloud calls, write precise owner-facing steps for IAM Identity Center Dashboard → verify organization/account instance → users/groups → permission sets → AWS accounts → account assignment; select the ACTUAL assigned user and a carefully scoped permission set. Retry SSO only after the assignment is visible in AWS access portal. Do not install other Python versions or recreate the application to work around this IAM blocker.

## 18.3 AWS architecture and operating constraints remain unchanged

- Preserve Enigma Next.js 16 + FastAPI Python 3.13 control plane, Cognito auth, DynamoDB Track B, CloudWatch operations dashboard draft, admin-only AWS page, security tests, PostgreSQL fallback and legacy SQL. AWS hosts **no inference and no GPU**. **ONLY Kaggle user-launched 2 × Tesla T4** run Stokastisk v0.1.0 build-5/vLLM 0.25.1 for authorized personal testing. No GPU cloud fallback or Amazon Bedrock/SageMaker; never touch the laptop GeForce 940M, including `nvidia-smi` or NVML calls. Local GPU test doubles only.
- Only `/usr/local/bin/python3.13` may run Python locally; `-m pip` for approved packages. No `uv`, `ruff`, `venv`, conda, Go or system Python fallback.
- Private `stokastisk-ai/enigma` GitHub only. Normal reviewed source commits and pushes after secret scan permitted, but no force pushes, history rewriting, repo privacy changes, accidental overwritten README, staging secrets or committing `.enigma-local/`.
- DynamoDB parity on a real AWS table, Cognito, Lambda Web Adapter, CloudWatch dashboard creation, live dashboard and any other AWS write remain **BLOCKED** until: non-root IAM access, explicit region, verified current free eligibility and billing credit expiration, fully reviewed least-privilege policy, itemized manifest/costs, written per-operation owner approval and teardown verification. Identity Center initial administrative changes are the owner's supervised console action; general code-generation authorization is not AWS expenditure approval.
- Never assert zero spend, current resource inventory, live metrics, cloud deployment, root remediation, or real Kaggle acceptance without independent evidence. Preserve current test evidence as historical, not fresh validation.

## 18.4 Next Claude sequence

1. Inspect current Git diff and safely restore main README, preserving bootstrap documentation separately.
2. Audit the uploaded old Section 17 charter, existing AWS docs, source, IaC and tests. Confirm exact account-assignment cause in AWS console guidance without requesting secrets or using root credentials.
3. Run the offline inventory tool, keep the result under ignored `.enigma-local/`; update `.gitignore` only with `/.enigma-local/` if missing.
4. Assist the owner with correct Identity Center organization instance, user, permission set and account assignment. After it is genuinely available, configure `enigma-deployer` profile using existing `p1-engima` session. Run read-only non-root STS validation.
5. Complete safe offline IAM hardening and application tests; keep AWS writes blocked. Run scoped no-GPU mock tests, then reviewed private Git fast-forward commit/push if appropriate.
6. Report SSO login OK vs assignment BLOCKED/VERIFIED, exact Git changes, tests, AWS calls and writes (none), region/expiry approval unknowns, account non-root verification, Kaggle and real DynamoDB status. Never re-ask user for already-known account number or SSO session name.


---

# 19. ENIGMA NEXT.JS 16 AWS AMPLIFY HOSTING + PRIVATE GITHUB EXECUTION CHARTER (2026-11-22)

**Precedence:** This section supersedes conflicts in Sections 1–18. Retain earlier sections for history and preserve source. This is a deployment-preparation mandate, NOT blanket authority to incur AWS charges or ignore compatibility gates. The root `CLAUDE.md` must tell Claude to read this full `skills.md`, especially Section 19.

## 19.1 Confirmed identity and separation

- On 2026-11-22 the owner ran `aws sts get-caller-identity` with `--profile enigma-deployer` and `--profile vllm-temp`. Both returned the same account (ID intentionally NOT recorded in Git; read it from `aws sts get-caller-identity` when needed) and the same assumed-role ARN containing `AWSReservedSSO_EnigmaDeployer_...`; the STS identity is therefore non-root for both profiles **at that time**. This does NOT prove root keys are deactivated, IAM policies are least privilege, or AWS deployment billing is approved. Validate current STS again before any cloud action and check authorization to requested services independently. Do not log full account identifiers or role ARNs in published reports.
- Preferred dedicated profile for Enigma: `enigma-deployer`; preferred region candidate `us-east-1`. In the owner's shell `AWS_PROFILE` may still be set to `vllm-temp` after their last command. **Do not rely on ambient AWS_PROFILE.** Every AWS service command must pass `--profile enigma-deployer --region us-east-1` where supported; verify actual profile/session before use. Do not modify or delete `vllm-temp` or old SSO sessions automatically.
- `vllm-temp` was associated with `sso_session=stokastisk-ai`, `sso_role_name=EnigmaDeployer`; obsolete `login_session` was removed from AWS config with a backup. The session name `waqas-engima` appeared earlier but do not assume Enigma profile uses that session; inspect `aws configure get sso_session --profile enigma-deployer` read-only. There is no reason to create another Identity Center user or role simply to rename a session.
- Never call AWS using default/root profile and never use root access keys to repair SSO. AWS key rotation/deactivation is owner-supervised **only after** a working non-root emergency/recovery path. SSO identity is not proof of least-privilege policy.

## 19.2 Immutable environment and privacy constraints

- Work only within existing proprietary root `/media/waqasm86/External1/Waqas-Projects/Project-Enigma/`; preserve Git history and the PRIVATE `https://github.com/stokastisk-ai/enigma.git` repository. Do not alter repo privacy, publish source, force-push, reinitialize, or delete user files.
- Use **only** `/usr/local/bin/python3.13` for local Python; no `uv`, `ruff`, virtual environments, Poetry, Conda or Go. Existing npm/Node build workflow permitted.
- Do not query or initialize the local NVIDIA GeForce 940M, `nvidia-smi`, NVML, CUDA, `torch.cuda` or vLLM. Only a user-launched Kaggle session with 2× NVIDIA Tesla T4 (SM75) may run Stokastisk 0.1.0 build 5 + vLLM 0.25.1, subject to Kaggle permission. No AWS GPU inference/EC2 GPU, Bedrock, SageMaker or alternative inference API; AWS is web/auth/storage/telemetry control plane only.
- Keep sensitive AWS settings, SSO token caches, AWS access keys, `.env.local`, temporary build data, `.enigma-local/` and any deployment output with secrets OUT of Git and public reports. Use role/session short-lived credentials, never permanent access keys for CI. Do not read or log `~/.aws/credentials` or cached access tokens as part of normal diagnostics.

## 19.3 Amplify Next.js 16 is an explicit HARD compatibility gate

- Inspect actual `apps/web/package.json` (snapshot pinned `next: 16.4.6`), its server actions, routes, auth, middleware, SSR, cookies, streaming, image optimization, dependencies, and `infra/aws/amplify.yml`. Preserve Next.js 16; **never silently downgrade to Next.js 15**.
- As checked on 2026-11-22, official AWS Amplify Hosting managed Next.js SSR docs only promise support for Next.js 12–15: https://docs.aws.amazon.com/amplify/latest/userguide/ssr-amplify-support.html and https://docs.aws.amazon.com/amplify/latest/userguide/deploy-nextjs-app.html. The supported framework adapter/deployment specification may offer a path for other frameworks, but that is NOT automatically proof for Next.js 16: https://docs.aws.amazon.com/amplify/latest/userguide/server-side-rendering-amplify.html . Recheck official current documentation before proposing deployment.
- Conduct source-supported local CPU-only build and framework/adapter validation. Verify monorepo `appRoot: apps/web`, artifact baseDirectory, package-lock, correct working directory, current Node runtime (AWS SSR supports 20, 22, 24 as documented), standalone output, cookies, login callbacks, API routes, Web Adapter if applicable, server actions, redirects, hosts, security and nonnative binaries. Do NOT assume Amplify Hosting supports Lambda Web Adapter deployment automatically; Lambda Web Adapter is a distinct architecture from managed Amplify SSR.
- Audit `infra/aws/amplify.yml` before editing. The earlier draft appends selected environment variables using `env | grep ... >> .env.production`; this can accidentally collect and propagate values, create stale build-time credentials, and potentially leak secrets. Replace with a reviewed explicit allowlist approach only if necessary and permitted by AWS deployment requirements; never write actual secret values into Git, artifact bundles or build logs. Avoid guessing from file names.
- If official compatibility/validated adaptation cannot be demonstrated without a paid deployment, mark **AMPLIFY_NEXT16_BLOCKED** and present another AWS control-plane approach as an explicit costed proposal. Do not downgrade or deploy an unsupported app merely to meet a deadline.

## 19.4 Billable deployment authorization: separate from coding/Git

- The user asks to deploy Enigma, but a verified real-time credit expiry, exact cost ceiling, approved itemized manifest and full resource dependency forecast are NOT supplied in this request. Thus these instructions authorize **local assessment, code fixes, safe test runs, read-only AWS calls and reviewed PRIVATE Git commits/pushes**, but NOT creation/update/deletion of AWS resources until the user gives explicit resource-by-resource approval after seeing cost risk. Credits and Budget alarms are NOT hard caps.
- Read current account plan and AWS Billing **Credits expiration** from the owner-controlled console when available; do not invent or derive from creation date. Document credit/service eligibility and unbounded-billing risks. Identify a chosen region and all billable components before asking for approval: Amplify hosting/build/SSR/egress, Git integration, Lambda FastAPI, Cognito, DynamoDB, logs, CloudFront, API Gateway/Function URL, S3, domains, environment/secrets and related requests. Never create an Amplify app, connect auto-build GitHub branch, start a build, call CloudFormation, deploy Lambda/Cognito/DDB or set up monitoring solely as 'free tier'. A GitHub push can trigger an already-connected Amplify build: **inspect whether automatic AWS deployments exist before pushing**, and do not push a trigger branch if it would cause an unapproved billed build.
- Prepare `infra/aws/resource-manifest.yaml` or new plan document with exact proposed identifiers/types, projected utilization, AWS price links, region, billing uncertainty, teardown and approval state. Treat all states as UNAPPROVED until owner explicitly authorizes named AWS writes and an acceptable spending-risk ceiling. Stop on unknowns.

## 19.5 Private GitHub synchronization and repo security

- Work from the actual local Git checkout. Verify `git status --short`, branch, `git remote -v`, `git fetch`/remote head read-only, authenticated `gh repo view stokastisk-ai/enigma --json isPrivate,nameWithOwner,viewerPermission` if available, and fast-forward relationship. A 404 from another connector does not mean repo absent; do not make it public as a workaround.
- Restore the application's main `README.md` if still overwritten by the AWS bootstrap helper; preserve the helper under `docs/` first. Review all changes and owner-untracked files. Keep `.gitignore` effective for `.enigma-local/`, node_modules, `.next`, secrets, archives, AWS cache, generated binaries, model weights and notebook outputs. Scan staged content for secrets; investigate findings without printing raw values.
- If existing GitHub Actions or Amplify automatic builds run on `main`, do not push changes until those side effects and spending risk are reviewed and approved. If no AWS auto-build is attached, normal reviewed, secret-free Git fast-forward pushes to existing PRIVATE repo are authorized after relevant tests. Do not force push or rewrite history; never push public Stokastisk repo.

## 19.6 Incremental work and proof gates

1. Read `CLAUDE.md`, complete `skills.md`, `.claude/skills/enigma-aws-deploy/SKILL.md`, current Git, `docs/AWS_*`, `infra/aws`, `apps/web`, `apps/api`, `connector`, tests and `.env.example`. Inspect actual source, do not treat previous success counts as current truth.
2. Verify `enigma-deployer` via STS assumed-role (read-only), identify Identity Center permission set and effective permissions without requesting keys. Audit default/root credential risk without using root.
3. Investigate Next.js 16 managed Amplify support and possible adapter integration with verifiable evidence. Fix and validate local `amplify.yml` only after checking real monorepo paths. Keep Lambda Web Adapter as **separate alternative proposal**, not a synonym for managed Amplify hosting.
4. Fix source/config and test local no-GPU flows: `/usr/local/bin/python3.13 -m pytest`, SQL suites, npm typecheck/lint/build/Node unit, Playwright mock E2E as supported. Check for GPU import side effects before tests. No live AWS/Kaggle claims from mocks.
5. Audit Cognito production auth, membership, token verification/JWKS, CSRF, cookie/redirect hosts; DynamoDB real-service parity is still NOT RUN until independently verified. Do not initialize cloud infrastructure during tests.
6. Prepare a costed, explicit deployment resource manifest and blocked/approved decision matrix. If bills/compatibility are unverified, stop AWS provisioning, but continue permissible source/tests/docs. Do not ask user for already established account identity or session name.
7. Inspect whether Git pushes would trigger auto-deploy; after safe verification, stage explicit source paths, review, commit/push PRIVATE repo via non-force update when permitted, then verify remote SHA and privacy. Otherwise leave changes local and report trigger blocker.
8. Once **separately authorized later**, create only the approved AWS resources, verify actual Amplify URL and Next.js 16 functionality with authentication, API/DynamoDB, bounded logs, error/latency, and teardown; test Kaggle 2×T4 only when owner starts notebook and provider permits. Never claim full E2E without evidence.

## 19.7 Final report (required)

Report matrix: source and current private Git SHA/remote sync; exact Python interpreter; local 940M untouched; SSO profiles and nonroot identity; root-key removal pending/verified; AWS account plan, credits and expiry; Next.js 16 Amplify compatibility official evidence and test results; `amplify.yml` changes; frontend/backend tests; actual AWS creations/changes/deletions and any charges; GitHub auto-deploy status; DynamoDB moto vs real; Cognito live status; CloudFront/Amplify URLs verified or NOT DEPLOYED; Kaggle build-5 live/not run; missing approvals and next steps. State UNCERTAIN/NOT RUN rather than inventing success.

## 19.8 Addendum (2026-11-22 audit, commit-safe)

- **No account identifiers in Git.** Never write the AWS account number, full role ARNs, SSO start URLs or profile cache contents into any tracked file; the account ID was removed from this section and is absent from the repository history (verified with `git log -S`). The untracked owner helper `scripts/enigma_aws_setup.py` still contains it: redact before ever staging it.
- **Evidence docs:** `docs/AWS_AMPLIFY_NEXT16.md` (support decision), `docs/AWS_WEB_TIER_COMPARISON.md` (Lambda Web Adapter vs custom Amplify bundle), `docs/AWS_DEPLOYMENT_READINESS.md` (inventory, exposure, teardown, approvals). Both web-tier paths stay in the repo until the owner approves one.
- **External triggers:** the org has a Vercel GitHub App with access to all repositories and `push` events. Commit `2c8d202` produced no status, check-run or deployment, so no project is linked today; the owner should confirm in the Vercel dashboard. No AWS-side trigger exists (0 Amplify apps, 0 stacks, 0 webhooks, 0 workflows).
- Local passing of `scripts/build_amplify_bundle.py` or `scripts/validate_nextjs_lambda.py` is never evidence of real AWS compatibility.

## Section 20 — Enigma $20 monthly AWS spending monitor (2026-11-22)

This section supplements all prior non-root, privacy, GPU, Kaggle, GitHub and AWS deployment rules. It does not grant deployment approval and never overrides a stricter prohibition in existing sections.

### Exact owner intent
- Protect approximately $99 remaining AWS promotional credits. The owner wants approximately 1–2-hour user-started Kaggle 2x Tesla T4 inference sessions, not a continuously running AWS server.
- The requested **USD $20/month AWS Budget** is a *warning/monitoring target*, **not an enforced spending cap**. Do not claim a notification budget guarantees maximum charges. AWS costs can be delayed by 8–12 hours or more, and ongoing charges may exceed a threshold before alerts.
- Do not presume that the owner has authorized $20 spending; a $20 budget is not approval to deploy or spend $20. Deployment requires the prior charter's separately reviewed resource manifest, region, credit expiry/eligibility, cost risk and explicit owner approval.

### Configuration requirements
- Account-wide monthly *recurring*, *fixed* **Cost** budget named `Enigma-Monthly-USD20`, USD 20, scope **All AWS services**, metric **Unblended costs** to alert on gross consumption prior to credits. Confirm console metric semantics and available selections. Also inspect net charges and credit usage separately in Billing.
- Email alerts for **ACTUAL spend at 25% ($5), 50% ($10), 75% ($15), 100% ($20)**; optionally 100% FORECAST if supported for monthly budgets. Confirm each email subscription rather than only creating thresholds.
- Do not enable automatic budget actions, SNS, paid tools or account restrictions unless separately evaluated and explicitly approved. Budget alerts and cost models are not hard limits.
- Verify Budget exists and is active in the AWS Budgets list, notification recipients, and billing reports; do not treat the editor preview as evidence of successful save.
- Verify credit expiry and applicability under Billing → Credits. Do not infer remaining credit from earlier $99.99 snapshot.

### Architecture and control
- The AWS Next.js 16 web tier and FastAPI Python 3.13 API must use only on-demand request metering without provisioned concurrency, AWS GPUs, RDS, NAT, ALB, paid WAF or idle EC2. Preserve Cognito, DynamoDB on-demand, and Kaggle-only Stokastisk runtime.
- Keep app protected during Kaggle-offline periods. Neither an Amplify disabled auto-build setting nor stopping Kaggle disables an Amplify SSR site.
- Continue using only `/usr/local/bin/python3.13`; never touch the laptop GeForce 940M. Use the explicit non-root SSO profile `enigma-deployer`, proposed region `us-east-1` (not deployed/approved by this section).
- The `scripts/enigma_aws_budget_audit.py` utility is strictly read-only for AWS. Never switch to root/default access if a billing API returns AccessDenied. Never publish account IDs, keys, credentials, billing files or account-specific metadata to public GitHub or prompts.
- Avoid writing raw secrets into CloudWatch, `.env.production`, artifacts, or Git. Preserve private `stokastisk-ai/enigma` and existing history; reviewed normal fast-forward pushes are allowed only when safe.

### Reporting and gates
- Report each check as confirmed by AWS API, confirmed by billing screenshot, locally tested, or unknown. Do not equate a dry run, a fake test runner, or a forecast with actual AWS cost exposure.
- Before deployment, confirm: Budget active and recipients confirmed; credit expiry and plan; estimated monthly *maximum risk* (not guaranteed cap); AWS Region; full resource inventory and teardown; explicit owner approval; safe Lambda Function URL permissions and public-endpoint abuse controls.

## Section 21 — Verified $20 budget state (2026-11-22, read-only AWS evidence)

- `Enigma-Monthly-USD20` exists: COST, MONTHLY, 20 USD, recurring, healthy; one alert only (ACTUAL > 25 % = $5, 1 e-mail subscriber). `My Zero-Spend Budget` ($1) is intact. No budget actions exist. Recommended extra ACTUAL alerts at $10/$15/$20 are NOT configured; the owner adds them in the console. Never create a second $20 budget; never modify budgets from code.
- **Both budgets use `IncludeCredit: true` (net of credits)**, so they will not alert while credits absorb charges. Owner should untick *Credits* in the console.
- Plan API: `PAID`/`ACTIVE`, remaining plan credits 0.0 (conflicts with the ~$99 the owner saw). `billing:GetCredits` is denied ("IAM user access not activated"). Credit balance, expiry, eligible services and Free Tier applicability are `UNVERIFIED — AWS BILLING CONSOLE REQUIRED`. Never estimate an expiry, never use root keys to read billing.
- Month-to-date (1–22 Nov): gross ≈ $0.00004, net $0. Nothing is deployed.
- `scripts/enigma_aws_budget_audit.py` exit codes: 0 verified, 2 refused, 10 missing, 11 misconfigured, 12 permission denied, 13 unverified; output never contains account IDs, ARNs or e-mails. Details: `docs/AWS_BUDGET_VERIFICATION.md`.
- API Lambda memory in `infra/aws/template.yaml` aligned to 512 MB (manifest and cost model already assumed 512).
- Open pre-deploy item: confirm whether public Function URLs now also need a `lambda:InvokeFunction` permission statement (docs/AWS_SESSION_COST.md §4).

## Section 22 — Next.js 16 Amplify-first repair and static-export evidence gate (2026-11-23)

**Scope and precedence:** Addendum to the owner's current `skills.md`. It supersedes older instructions only regarding the choice of Next.js 16 Amplify deployment mode and local Python interpreter. All stricter security, billing, Git privacy, AWS non-root, local GPU isolation and explicit AWS-write-approval rules remain in force. Preserve the entire previous document and do not duplicate this section.

### Product and interpreter
- Enigma is an authenticated **dynamic Next.js 16.4.6** observability and chat application connected to separate FastAPI **Python 3.13** on AWS Lambda, Cognito, DynamoDB and the user-started Kaggle Stokastisk connector. Its AWS web tier is requested on **Amplify Hosting**. Preserve runtime auth/BFF, project separation, dashboards, chat, historical metrics, callbacks, cookies and dynamic routes.
- Local interpreter **MUST be `/usr/local/bin/python3.13`**. `python3.11` is not installed. The filename `enigma_amplify_preflight_py311.py` denotes the previous compatible helper, not the project's runtime. Do not install or invoke Python 3.11. No `uv`, `ruff`, venv/virtualenv, Poetry, Conda or local CUDA/GPU access.
- The laptop's GeForce 940M must never be probed. Never create GPU services or run vLLM on AWS. Only user-launched Kaggle 2 × Tesla T4 with Stokastisk build 5 can run inference.

### Source PDF scope
- Uploaded u11d PDF/article, **“Next.js 16 SSG on AWS Amplify: A Practical, Cost-Effective Deployment Guide”** (Jan 30 2026), documents **static export only**: `output: 'export'`, `images.unoptimized: true`, `amplify.yml` `baseDirectory: out` and a static hosting flow. The PDF explicitly says this approach has **no API routes, SSR, ISR or per-request dynamic behavior** and dynamic paths need build-time `generateStaticParams`. It does NOT demonstrate Amplify managed SSR for Next.js 16.
- Never copy its `next.config.*`, `out/` artifact setting, catch-all rewrite to `index.html`, or `git add .` into the current authenticated Enigma app without a full route/auth/API compatibility audit and regression proof. Its SSG instructions may apply to an independently authorized static-only variant, not silently to the current application.
- Current AWS official managed SSR docs describe **Next.js 12–15**. The custom `.amplify-hosting` deployment specification may offer a route for 16 but requires real AWS validation. Links: https://docs.aws.amazon.com/amplify/latest/userguide/ssr-amplify-support.html and https://docs.aws.amazon.com/amplify/latest/userguide/ssr-deployment-specification.html . Recheck before conclusions.

### Repair workflow and mandatory deliverables
- Read current `CLAUDE.md`, ALL `skills.md`, local Git state, frontend/backend/connector sources, `infra/aws/amplify.yml`, builder, `apps/web/next.config.*`, AWS manifest, the preflight JSON and tests. Verify rather than trust older SHA/test counts.
- Preflight observations: Next.js 16.4.6; missing root `amplify.yml`; `enigma-deployer` role SSO verified; **zero Amplify apps returned for `us-east-1`**. There is no evidenced live Amplify deployment. Missing root buildspec is a local configuration finding, not by itself proof of a failed hosted build.
- Use reviewed **repository-root `amplify.yml`** consistent with AWS monorepo rules and `AMPLIFY_MONOREPO_APP_ROOT=apps/web`; verify `buildPath`, `appRoot`, baseDirectory, lockfile and all build commands. Never blindly copy the existing nested buildspec without validating it. Official reference: https://docs.aws.amazon.com/amplify/latest/userguide/monorepo-configuration.html .
- Create `docs/AWS_AMPLIFY_NEXT16_ROUTE_MATRIX.md` documenting every route's SSG/SSR/API/auth requirements. Preserve dynamic Next.js 16 if any required functionality is incompatible with export. Validate the existing custom compute bundle with official manifest format, port, deployment directory, cookies, redirects, BFF, Cognito, environment/secret constraints and local SSR tests. Maintain Lambda Web Adapter alternative in source for contingency.
- Do not assume `python3.13` exists in AWS Amplify's hosted build image. Document reproducible setup or remove Python dependency from its Node build pipeline without changing FastAPI's own Python 3.13 runtime.
- Run fresh CPU-only tests, Next.js build/typecheck/lint, Node tests, Playwright mock E2E, secret checks and governed buildspec validations. Record results accurately and distinguish locally supported, cloud unverified and blocked.

### Security, AWS and billing gates
- **Never reproduce exposed temporary SSO role credentials** from earlier screenshots, write them to files, or use them for development. Owner must invalidate compromised role sessions according to official guidance; preserve non-root `enigma-deployer` SSO login and do not use root/IAM-user keys.
- No Amplify app creation, connection of private GitHub to auto-build, CloudFormation/CloudWatch `PutDashboard`, IAM modifications, DynamoDB real-table tests, budget modifications or other AWS writes until explicit per-operation approval, region, resource manifest, credit expiration, estimated maximum charge risk and teardown are all reviewed.
- The existing USD $20 monthly budget is notification-only; a previously reported remaining promotional credit balance is NOT current proof of usable credit or expiry. Treat both budget alert configuration and availability of free-tier offsets as independently verifiable.
- Enigma's proprietary repo stays PRIVATE at `stokastisk-ai/enigma`. No forced pushes, broad `git add .`, secret-bearing artifacts, `.enigma-local`, credentials or model binaries in Git. Check for external auto-deploy triggers before even an otherwise authorized source push.

### Decision classifications
- `STATIC_EXPORT_SAFE` only with complete functional route/Auth/BFF proof.
- `CUSTOM_AMPLIFY_COMPUTE_READY_FOR_CONTROLLED_TEST` only when local package, deployment spec and runtime smoke tests pass; still **not AWS-deployed**.
- `BLOCKED_AMPLIFY_NEXT16` when official/custom compute issues remain; preserve alternate Lambda Web Adapter code for later owner choice.
- Do not claim success without real URL and full authentication, Cognito, API, DynamoDB and Kaggle-to-AWS acceptance, all independently evidenced.

## Section 23 — Enigma repository hygiene, Amplify 16 audit fixes and controlled cleanup (2026-11-23)

**Scope:** Append this section once to the *current local* `skills.md`. Do not replace the user's existing instructions or disable earlier security, deployment and billing rules. For conflicts, the stricter no-AWS-write, no-local-GPU, secret-protection and owner-file preservation controls apply.

### Non-negotiable state
- Keep Python 3.13 at `/usr/local/bin/python3.13`, Next.js 16.4.6, private `stokastisk-ai/enigma`, AWS SSO profile `enigma-deployer` in `us-east-1`, Cognito/FastAPI/DynamoDB, and Kaggle-only Stokastisk build-5 / dual Tesla T4 inference. Never probe the laptop's NVIDIA 940M or execute AWS GPU services.
- Latest **reported**, not independently rerun, assessment: `CUSTOM_AMPLIFY_COMPUTE_READY_FOR_CONTROLLED_TEST`; Git `1facf4b`; root `amplify.yml`, `scripts/build_amplify_bundle.mjs`, route matrix and governance tests changed locally **without commit/push**. Verify current HEAD and exact diff before proceeding.
- Amplify managed SSR still documents Next.js 12–15, not 16. The custom `.amplify-hosting` artifact passed local validation only; no real AWS deployment or service compatibility is proven. Static export is not a drop-in replacement for authenticated SSR/BFF.

### Mandatory safety for cleanup
1. **Run read-only inventory first**: `/usr/local/bin/python3.13 scripts/enigma_cleanup_inventory.py --project "$PWD"`. Inspect `.enigma-local/cleanup-candidates.json` and actual `git status`, `git ls-files`, `git grep`, imports, README links, packaging and tests.
2. Classify each candidate with evidence as KEEP, ARCHIVE_OUTSIDE_REPO, REMOVE_FROM_REPO or UNKNOWN. Provide exact paths, tracked/untracked status, references, replacement, restoration method and test impact.
3. **Do not automatically delete candidates**. A candidate flagged by name is not proof of redundancy. Do not delete or relocate user-owned/untracked files without explicit owner instruction that identifies the category or paths; preserve locally until approved. Do not bulk `rm -rf`, `git clean -fdx`, `git reset --hard`, `git add .`, rewrite history, force push or remove unknown secrets using destructive methods.
4. Preserve source, tests, `.gitignore`, present `skills.md` and `CLAUDE.md`, root `amplify.yml`, deployment docs, active builders, AWS manifests and templates, CloudWatch tools, offline connector, Kaggle notebook, and Lambda Web Adapter fallback pending live success and signed-off rollback. Never remove `.enigma-local` or local owner backups without explicit consent.
5. Once deletion is separately approved: create a path-based rollback inventory, back up valuable owner files outside the repository with mode 0600/0700, stage *explicit* paths, run fresh tests, check diff, and commit only with permission. Never place private backups in Git.

### Mandatory engineering corrections
- The rate-limit test `test_rate_limit_returns_429_with_retry_after` is flaky with a real-clock token bucket (5/sec, burst 10, 15 requests); use fake/injected monotonic clock or deterministic transport/test fixture, preserving real production rate-limiter semantics and both in-memory/DynamoDB test coverage. Rerun full suite, not just the isolated test.
- Review refresh-cookie `Path=/api` versus `proxy.ts` page gating. Prove correct behavior across access-token expiration, reload and subsequent refresh using secure, `httpOnly`, `SameSite`, `Secure` and path-scoped cookie tests. Do not widen cookie scope casually; prefer minimal, audited auth-flow repair that preserves CSRF and tenant isolation.
- Verify that Amplify build artifact glob includes required hidden runtime files (`.env.production` if legitimately required), while never embedding build secrets or local `.env.local`/dev publishable keys. A runtime 503 on missing configuration is a safe failure, not a deployed success. Verify destination path against AWS artifact and monorepo specifications.
- Recheck root `amplify.yml` `appRoot=apps/web`, working directory, `buildPath`, `baseDirectory`, and hosted build Node availability; compare the pure-Node bundler output with Python validator. Never assert cloud support from localhost equivalence.
- Do not run any AWS writes (Amplify create/start, IAM permissions, Cognito/DynamoDB real table, budgets, CloudWatch dashboard, or teardown) without separate itemized and cost/credit/region approval. No Git push if it could trigger unapproved deployment.

### Completion report
Show source-linked cleanup decision table; exact diff and files retained; fresh `pytest`, typecheck, lint, Node, Playwright, local Amplify validation; any unresolved flake; auth-cookie and secrets evidence; Git commit status; and a separate list of approvals still needed. No claims of real AWS or Kaggle end-to-end acceptance without live evidence.

## Section 24 — Enigma lean repository and non-destructive maintenance

This section supplements prior instructions; preserve security, billing, authentication, deployment, and Kaggle-only inference constraints in the complete current skills.md.

**Authority:** The owner requested a smaller, clean, reproducible Enigma repository, not unconditional deletion. All operations that discard or move user-owned content require explicit approval for the *exact file list*. Do not infer such approval from the existence of a cleanup prompt, audit, or script.

**Environment:** `/media/waqasm86/External1/Waqas-Projects/Project-Enigma/`, Python `/usr/local/bin/python3.13`, Next.js 16, AWS non-root profile `enigma-deployer`, Region `us-east-1`. AWS writes, deployments, spending, IAM changes, and local GPU access remain forbidden without separate authorization.

**Required order:** (1) inspect fresh Git state and instructions, (2) measure sizes and nested cache footprint, (3) distinguish regenerable caches, dependencies, tracked source, owner notes, private files and artifacts, (4) run tests / clean-checkout validation, (5) produce a per-file or per-directory cleanup proposal with size, tracked status, references and rollback plan, (6) request explicit approval before deletion or archival.

**Tools:** `scripts/enigma_disk_audit.py` is read-only. `scripts/enigma_cache_cleanup.py` is preview-only by default and deletes only allowlisted regenerable cache directories with an explicit execution phrase; do not run its execution mode without owner approval. Treat `node_modules` removal as higher-risk because package-manager reinstallation may take time/network access.

**Protected material:** Keep `.git`, `skills.md`, `CLAUDE.md`, `.enigma-local`, `.env.local`, active Next.js/web/FastAPI/connector code, manifests, security documentation, deployment templates, tests, the Amplify bundle builder/validator, and Lambda Web Adapter fallback. Avoid revealing secrets, account IDs or credentials in reports, commits or console transcripts. Remove no backups until their unique content and recovery value have been assessed.

**Deployment readiness:** Local Next.js 16 `.amplify-hosting` validation does not prove live AWS Amplify SSR support. Do not set `output: 'export'` for the current dynamic application merely to make static hosting work. Never call cloud deployment/write APIs without separate itemized approval and budget/credit verification.

**Evidence:** Write `docs/ENIGMA_LEAN_MAINTENANCE_REPORT.md` with measured sizes, exact proposed removals, fresh tests, Git status, reversibility and outstanding blockers. Label synthetic tests, unverified claims and unpublished changes accurately.

## Section 25 — Browser/CLI identity isolation, Amplify readiness, and conservative cleanup

1. AWS access portal `EnigmaDeployer` console sessions and AWS CLI `enigma-deployer` profile are distinct from legacy IAM-user console sessions. Diagnose the actual principal before suggesting IAM changes. Prefer SSO Management Console sign-in, never copy access keys from the Get credentials screen.
2. Run only explicit non-root SSO read-only AWS inspection absent separate owner approval. Never change IAM, budgets, CloudWatch dashboards, Amplify apps, CloudFormation, DynamoDB, Lambda or Cognito on behalf of a preflight.
3. Never display or commit credential strings, session tokens, AWS account identifiers, identity email or private reports. Exposed temporary credentials require owner remediation.
4. Preserve Next.js 16 dynamic/SSR architecture and its Cognito/FastAPI/DynamoDB/Kaggle flows. `output: 'export'` is forbidden on the current dynamic application. Treat `.amplify-hosting` custom bundle success as local evidence, not live provider acceptance. Retain Lambda Web Adapter fallback.
5. For repository slimming, inventory exact file paths and references, test a clean build, identify Git tracking and backup hashes, and propose approval-separated cleanup phases. No deletion, moving, `git rm`, `git clean -fdx`, git push or commit without separate explicit authorization. Keep security notes, active source, tests, private data, and owner files. Never clean the parent `Waqas-Projects` directory.
6. Default to CPU-only Python 3.13 and local safe regression tests; no GeForce 940M GPU probes and no AWS/Kaggle GPU inference initiated from local cleanup.
7. The $20 monthly AWS Budget is an alert threshold and does not cap spend. Require credit expiration, an itemized resource manifest, explicit spending ceiling and teardown approval for a live Amplify test.

## Section 26 — Enigma Railway migration and controlled legacy deployment retirement (2026-11-24)

### Scope and source of truth
- Repository: `/media/waqasm86/External1/Waqas-Projects/Project-Enigma/`; web `apps/web`, API `apps/api`, connector `connector`, contracts `packages/contracts`.
- Read the complete live `skills.md` and `CLAUDE.md` first. This addendum is subordinate to stricter security/owner instructions. Uploaded GitHub ZIPs may predate uncommitted local work and MUST NOT be used to overwrite the working tree.
- Goal: one Railway project with separate Next.js 16/React 19 and FastAPI/Python 3.13 services, preserving authenticated BFF and long-running backend; optionally use AWS DynamoDB with explicitly approved credentials/charges. Keep Kaggle dual Tesla T4 the only LLM inference runtime.
- User's Railway Hobby workspace has an included $5/month resource credit; it is not a spending cap. No service creation, deployment, database provisioning, paid resource usage, public DNS change, Git push, or cloud resource deletion without separate, explicit and itemized owner approval.

### Non-negotiable safeguards
1. Never initialize/query laptop NVIDIA GeForce 940M, install local CUDA/vLLM, invoke `nvidia-smi`, or create Railway/AWS GPU inference.
2. Never display, export, commit, or upload credentials, session cookies, access tokens, private keys, actual account IDs, `.env.local`, `.aws/`, `.enigma-local/`, or reports with secrets. Do not copy raw provider credentials into files or prompt.
3. No blanket `rm -rf`, `git clean -fdx`, `git reset --hard`, `git push`, deletion of branches/history, or automated changes to Vercel/AWS/Railway control planes without approval.
4. Keep the current verified Clerk and Cognito paths until a single selected auth provider passes real end-to-end Railway tests. Preserve httpOnly cookie scope, CSRF, origin checks, redirect allow-lists, JWT validation, tenancy, limits, and fail-closed defaults. Do not enable development authentication in Railway or AWS production.
5. Never remove Lambda Web Adapter, working `amplify.yml`, infra AWS SQL/DynamoDB implementation, Vercel configs, migration scripts, or rollback documentation until the Railway target passes the explicit rollback/retirement gate. A user request to 'delete deployments' does NOT authorize deletion of actual live resources, IAM roles, budgets, AWS account, GitHub history, or credentials.
6. Preserve open-source Stokastisk SDK and GitHub Pages documentation in their independent repositories; do not consolidate or deploy GPU runtime in Enigma.

### Work phases (mandatory order)
**R0. Inventory and classification, read-only.** Capture repo branch/HEAD/status, tracked/untracked/ignored files, deployment-related references, current local authentication/worker protocols, package builds, Python requirements, database settings, secret names (names only), and active cloud inventories (only if credentials exist and read permission is granted). Explicitly mark resources as LIVE / LOCAL CONFIG ONLY / UNVERIFIED. Create `docs/RAILWAY_MIGRATION_INVENTORY.md` and per-file table: path, tracked?, referenced by?, action KEEP/CONVERT/ARCHIVE CANDIDATE, impact, backup verification. Do not delete yet.
**R1. Architecture and cost gate.** Document HTTP BFF `apps/web/app/api/enigma/[...path]/route.ts` -> private FastAPI `api/v1/...`; public Kaggle connector -> authenticated HTTPS ingress; auth and CORS/Origin; persistent DB; migrations; health endpoints and metrics. Recommend railway web + railway api, with Railway Postgres as a conditional third service OR existing AWS DynamoDB if an approved table already exists. A memory store is NEVER a production database. Compare actual Railway per-minute cost estimates for two always-on services including RAM, CPU, traffic, optional DB, and fail under owner's spending ceiling. Add railway spend alerts/hard usage controls when available.
**R2. Railway configuration, source-only.** Produce reviewed, minimal service configuration in `apps/web/railway.toml` and `apps/api/railway.toml` (or exact justified alternative), with `apps/web` and `apps/api` root directories, deterministic build/start, port binding `0.0.0.0:$PORT`, healthcheck, restart policy, supported Node version and Python 3.13. Since service root confines build context, verify whether `apps/web` imports `packages/contracts` from outside root. If so, retain repository-root build context and explicit Dockerfile/commands rather than breaking imports. Check whether Railpack supports pinned Python version before selecting explicit Dockerfile. Do not assume `npm start` starts standalone output or that Python dependencies from `requirements-aws.txt` work unchanged. No platform-specific AWS/Vercel environment tricks in production.
**R3. Security and auth.** `ENIGMA_DEV_AUTH` off; no permissive wildcard CORS or raw secrets in `NEXT_PUBLIC_*`; secure httpOnly cookies; public `ENIGMA_PUBLIC_ORIGIN` and configured auth callback URLs; server-only API base to Railway private DNS/port; provision `ENIGMA_API_BASE` or actual app-defined variable only after checking `apps/web/lib/server/env.ts`; correct `X-Forwarded-*` trust; credentials verified server-side; no frontend direct DynamoDB or Kaggle access; protect all runtime/telemetry operations.
**R4. Persistence.** For PostgreSQL, audit `PostgresStore` against `infra/aws/sql` and `supabase/migrations` and test migrations on disposable local PostgreSQL, without touching real production DB. Set `ENIGMA_STORE=postgres`, `ENIGMA_DB_DSN` only in Railway secrets; use environment-specific TLS settings and confirm internal DNS/port and no public DB exposure. For DynamoDB, verify `ENIGMA_STORE=dynamodb` / `ENIGMA_DDB_TABLE`, correct AWS credential provider for Railway (short-lived federation preferred; long-lived secrets only with explicit owner security approval), IAM least privilege, region, actual existing table, usage charges and moto parity. Do not provision any new AWS service without approval.
**R5. Source and CPU-only validation.** Run Python 3.13 pytest with GPU-isolation, Node typecheck/lint/unit, production SSR build, Playwright mocks, auth and refresh-flow regression, package security audit, API/BFF contract tests, URL/origin/proxy regression, socket/port healthcheck, long-running API and reconnect tests, and secret scan. Test with app configured as railway production but no actual network/provider writes. Record reproducible commands and results.
**R6. Deployment approval and controlled canary.** Present exact Railway project/service names, desired private/public domains, selected repo+branch, build and start commands, environment-variable NAMES, expected number of services/volumes, itemized cost model, limits, rollback, and verification matrix. STOP and request authorization for Railway writes. Upon approval alone, create a staging deployment, run smoke and signed-in E2E, verify Kaggle outbound HTTPS handshake and telemetry, database durability, uptime, metrics, cookies, and rollback.
**R7. Legacy retirement gate.** Only after explicit owner approval AND verified Railway success: archive and propose deletion of specific AWS/Vercel deployment-only repository files. Do not automatically delete AWS IAM Identity Center, Cognito if still used, DynamoDB if still used, CloudWatch historical dashboards, budgets, or cloud account; cloud decommissioning is a separate authorized operation with export and rollback plan. Prefer removing old CI/deployment triggers first after verification; do not sever auth or data dependency. Stage exact file changes for review, and stop before commit/push unless approved.

### Required outputs
- `docs/RAILWAY_MIGRATION_INVENTORY.md`
- `docs/RAILWAY_ARCHITECTURE_AND_COST.md`
- `docs/RAILWAY_AUTH_DATABASE_CHECKLIST.md`
- `docs/RAILWAY_SOURCE_VALIDATION.md`
- `docs/LEGACY_AWS_VERCEL_RETIREMENT_PLAN.md`
- Minimal Railway service configs / Dockerfiles only after inspection and tests
- Terminal summary with unmodified original files, changed file paths, passing/failing/skipped tests, unknowns, exact proposed deletions, and separate approvals needed.

### User-facing decision defaults
- Default migration scope: railway web+api; existing Cognito/Clerk unchanged; no cloud AWS writes; PostgreSQL selected only after parity and cost approval; GitHub Pages and Stokastisk unchanged.
- GitHub `stokastisk-ai/enigma` was public when checked 2026-11-24; do not assume privacy, recheck. Never commit secrets.
- A healthy local build is NOT evidence of successful Railway deployment.

## Section 27 — Enigma Railway deployment authority, safety gates, and rollback (2026-11-24)

### Scope and source of truth
- Project: `/media/waqasm86/External1/Waqas-Projects/Project-Enigma/`; Git origin must be `https://github.com/stokastisk-ai/enigma.git`; source of truth is the newest local worktree, not archived ZIPs or an old remote SHA.
- Enigma = Next.js 16 web in `apps/web`, Python 3.13 FastAPI in `apps/api`, `packages/contracts`, and the Kaggle Stokastisk connector. Never initialize the local GeForce 940M, CUDA or a Railway/AWS GPU.
- Maintain secure authentication and per-tenant isolation. Development bypasses must fail closed in hosted environments. Preserve Clerk/Cognito compatibility, cookie/CSRF protections and BFF route allowlists.
- Keep AWS and Vercel artifacts until Railway passes documented end-to-end acceptance. AWS is optional only and has no standing deployment authorization.

### Railway access model
- User may authorize an Enigma-scoped Railway deployment, NOT account-wide authority. Obtain consent and only operate on the existing Railway Enigma project/environment and services, except expressly approved new API and database services in that same project.
- Favor `railway login` or a **project-scoped** `RAILWAY_TOKEN`. Never demand/print/store workspace-wide `RAILWAY_API_TOKEN`, Railway tokens, AWS access keys, Clerk secrets, database passwords or Kaggle secrets. Never ask to paste them in prompts. Keep local Railway link metadata ignored.
- Confirm linked project ID, environment, service, source repo, branch and the **actual** commit or uploaded source snapshot before any write. Never use `railway up --new`, `--yes` for implicit creation, or an unscoped deploy command. Check pending dashboard changes before applying.
- Railway's old `railway.toml` / `railway.json` Config as Code is deprecated; do not create one as a shortcut. Do not run `railway config apply` because it may change or remove infrastructure. Prefer dashboard settings and verified Dockerfiles.

### Build and runtime
- Keep repo root as Docker **build context** because web imports `packages/contracts`; configure service Root Directory `/` or blank. Choose explicit `RAILWAY_DOCKERFILE_PATH=apps/web/Dockerfile` for web and `apps/api/Dockerfile` for API; verify current Railway UI semantics and official docs.
- Source uses remote `main` only after reviewed migrations are committed and pushed. Local `railway up . --project <verified> --service <verified> --environment <verified>` can deploy unpublished code if the service is configured for the right context. Confirm upload includes Dockerfiles, lockfiles, contracts and Python requirements; don't upload `.env.local`, `.aws`, caches, secrets or private reports.
- Don't set a custom start command if the Dockerfiles have a correct CMD/ENTRYPOINT; test binding to `0.0.0.0:$PORT`. Expose frontend via Railway-generated HTTPS domain and FastAPI via separately authenticated HTTPS endpoint for Kaggle; use Railway private networking for web BFF to API where appropriate. Browser must not receive private DNS or server-only secrets.
- Host production gates must reject development auth. Validate origin/host trust behind TLS proxy, cookies, redirects, CSRF, tenant scoping, request limits, connector replay, offline states and telemetry provenance. Database persistence must survive restarts. No persistent in-memory production store.
- PostgreSQL deployment needs separately approved database provisioning, schema bootstrap and non-superuser runtime role; verify TLS and backups. If AWS DynamoDB retained instead, use least-privilege workload credentials, not user's interactive SSO session. A real public Kaggle connector must authenticate outbound to API; do not run Kaggle or GPUs without user launching it.

### Spending, rollback and boundaries
- Before first deployment, inspect Railway Hobby usage limits and set a conservative service spending/usage cap supported by the current plan. The $5 monthly subscription and $5 included usage are **not** a hard cap. Do not assume high vCPU/RAM plan allowances are provisioned resources. Start at 1 replica, modest CPU/RAM limits, no cron/paid volumes/DB without approval. Only choose Serverless/app sleep after checking connector polling and cold starts; it may not work if services continually poll.
- Allowed after explicit Enigma deployment approval: configuring the existing service, adding one web/API service in the existing project if necessary, assigning an HTTPS domain, setting scoped variables in Railway UI or via approved CLI, and performing a controlled Railway deployment/rollback. Require explicit additional approval to create Postgres or any other billable persistent resource or to affect other projects.
- Never perform AWS resource writes, change IAM/budgets, remove AWS/Vercel, change GitHub visibility, force push, delete live services/databases/volumes, or automatically push source. A Railway deployment approval is not permission for these actions.
- Require before deploy: signed-off project/environment/service IDs, exact manifest and config, validated secrets location (names only in reports), usage cap, build context, Dockerfile path, local tests, rollback target, and named acknowledgement of possible Railway charges.
- Validate health and readiness separately. Keep build/deploy logs sanitized, record deployment ID, test public routes and protected routes, and stop/rollback on authentication, data integrity, or cost anomalies.
- Archive obsolete deployment files only after Railway end-to-end tests and separately approved exact file list; preserve Git history and backup strategy.

### Expected output
- A deployment preflight report, service inventory, env-variable names-only matrix, local regression results, deployment or blocked status, exact costs/limits observed, public URL if successfully deployed, rollback instructions and outstanding integrations. Never claim success without a healthy deploy and HTTP evidence.

## Section 28 — Enigma production state on Railway, real Cognito, and permanent cleanup (2026-11-24)

**Precedence:** latest applicable section. It supplements §26–27 and supersedes any earlier text that describes Vercel, AWS Amplify, Lambda or CloudFront hosting, a placeholder Cognito pool, or the retired cleanup/migration prompts. Stricter privacy, GPU, secret and spending rules of earlier sections still apply.

### Current architecture (verified live 2026-11-24)
- Railway project `blissful-solace`, environment `production`: web `Enigma` (Next.js 16, Dockerfile `apps/web/Dockerfile`), `enigma-api` (FastAPI Python 3.13, `apps/api/Dockerfile`), `Postgres` (PostgreSQL 18, private network only). Both app services are GitHub-connected to `stokastisk-ai/enigma` `main`, repo-root build context, Serverless on, one replica, per-service watch paths. Railway cannot enforce a $5 total: Hobby bills max($5, usage); the workspace usage limit is soft $5 / hard $10.
- Identity: Amazon Cognito user pool `enigma-prod` (`us-east-1`, Essentials tier, email username, no MFA/SMS/SES, deletion protection on) with one public app client `enigma-web` (no secret; USER_PASSWORD_AUTH + refresh; token revocation). Created with the owner's explicit approval on 2026-11-24. Clerk and Supabase code remain as optional, unused providers.
- Database: the portable schema in `infra/aws/sql`, applied by `python -m enigma_api.bootstrap` (Railway pre-deploy; needs `ENIGMA_DB_ADMIN_DSN` only while bootstrapping, then delete it). The API connects as a NOSUPERUSER NOBYPASSRLS role over `sslmode=require`.
- Retention: `.github/workflows/retention.yml` (daily) calls `GET /api/v1/admin/prune` with `ENIGMA_CRON_SECRET`; there is no background worker anywhere, so web and API can sleep.
- Kaggle 2×T4 remains the only inference runtime. Mock connector runs are always labelled MOCK and never count as GPU evidence.

### Removed (do not recreate)
Vercel (`vercel.json`, `index.py`, starter SVGs, `VERCEL` guards), AWS Amplify (`amplify.yml`, bundle builders/validators), Lambda (`lambda_handler.py`, `requirements-aws.txt`), CloudFormation hosting templates, CloudWatch dashboard generators, AWS cost/teardown scripts, historical Claude prompts, section fragments, backups, generated reports, build outputs. Kept: Cognito code, the DynamoDB adapter and its two templates, the dormant AWS Ops admin page (`ENIGMA_AWS_OPS=off`; removal candidate), the read-only budget audit, IAM policy drafts, SQL.

### Standing rules learned
1. The GitHub repository is public until the owner changes it: commit only code, tests, Dockerfiles and public-safe docs; keep `skills.md`, `CLAUDE.md`, `docs/` internals and `.enigma-local/` out of Git.
2. Verify a commit by exporting the exact index to a clean directory and testing there.
3. Use the environment-patch API/dashboard for Railway configuration; never `railway.toml`, `railway config apply`, or `railway up .` from the repo root. Variables that hold secrets are set through stdin or reference variables, never printed.
4. AWS only via `aws sso login --profile enigma-deployer`; verify the `AWSReservedSSO_EnigmaDeployer` assumed role first; creating billable AWS resources needs separate approval.

## Section 29 — Neon-only authentication and storage; AWS/Supabase/Clerk/Cognito retired (2026-11-25)

Supersedes every AWS, Cognito, Clerk, Supabase, DynamoDB and Vercel policy in sections 1–28 for the application. Architecture: Railway (`enigma-web` Next.js 16, `enigma-api` FastAPI/Python 3.13, Dockerfile builds from the repository root, Serverless) + Neon PostgreSQL (project `enigma-neon`, branch `production`, db `neondb`, aws-us-east-2) + Neon Managed Better Auth. GPU inference stays on the user's Kaggle 2×T4; nothing GPU, CUDA, torch or vLLM ever goes into a Railway image.
- Code: `ENIGMA_AUTH_PROVIDER=neon` is the only accepted value; the API refuses production start without `NEON_AUTH_BASE_URL` (https), `ENIGMA_STORE=postgres`, a `sslmode=verify-full` DSN, `CRON_SECRET`, and verified-email enforcement. Dev HS256 auth remains local-only and is refused on Railway.
- Schema: `db/migrations/*.sql` (portable, no RLS; authorization is the API plus the SQL functions). Never run DDL against `neon_auth`. Use the direct endpoint for the app (the pooled endpoint rejects the store's startup `options`) and a separate admin DSN only for bootstrap.
- Never delete the stopped Railway `Postgres` service/volume, Neon data, or AWS/Vercel/Supabase accounts without explicit owner approval. Repository is PUBLIC: do not commit CLAUDE.md, skills.md, internal docs, IDs or credentials.
- Do not claim `DEPLOYED_AND_VERIFIED` until real sign-up/verify/sign-in, tenant isolation, wake-from-sleep and a Kaggle-connector claim have been run against production.
