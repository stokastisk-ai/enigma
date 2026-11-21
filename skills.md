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
