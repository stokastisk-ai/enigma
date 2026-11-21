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
