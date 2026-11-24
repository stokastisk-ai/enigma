# Enigma end-to-end data flow (source-level trace, 2026-11-22)

Browser → Next.js 16 BFF → FastAPI (Python 3.13) → store (DynamoDB) ← Kaggle connector → local vLLM (Stokastisk build 5). AWS is the control plane only; all inference runs on a user-launched Kaggle 2×T4.
**Status of every arrow below is SOURCE-TRACED and covered by CPU-only/mock tests. None has been exercised against real AWS or a real Kaggle GPU.**

| # | Hop | Implementation (file) | Contract | Evidence level |
|---|---|---|---|---|
| 1 | Browser → BFF | `apps/web/lib/api.ts` → `app/api/enigma/[...path]/route.ts` | same-origin `/api/enigma/<p>` → `/api/v1/app/<p>`; httpOnly Cognito cookies; CSRF host check; upstream body is buffered | mock Playwright (16), node unit (15) |
| 2 | Submit prompt | `POST conversations/{id}/jobs` (`routes_app.py:186`) | `Idempotency-Key`; returns `{job_id}` 202; job is stored `queued` for the conversation's runtime | pytest `test_jobs.py` |
| 3 | Connector claim | `POST /api/v1/jobs/claim` (`routes_runtime.py`) | scoped runtime token; lease owner header; `{job: null, poll_after_ms}` when idle; `next_event_seq` continues across attempts | pytest + `test_executor.py` (real localhost HTTP) |
| 4 | vLLM stream | `connector/jobs.py::StokastiskLlm` → `stokastisk.serving.stream_chat` | OpenAI-style SSE deltas from `127.0.0.1:8000`; truncated stream (no `[DONE]`) is an error | mock + SDK signature check (this session) |
| 5 | Ordered events | `POST /api/v1/jobs/{id}/events` | contiguous `event_seq` ≤64/request; gap → 409 `sequence_gap` + `expected_seq`; duplicates counted; retry starts with `reset`; cancel flag returned | pytest (DynamoStore on emulator, MemoryStore) |
| 6 | Completion | `POST /api/v1/jobs/{id}/complete` | `final_seq` + SHA-256 of the attempt's text, recomputed server-side; idempotent | pytest |
| 7 | Browser poll | `GET jobs/{id}?after_seq=` (`JobView`) | the browser **polls** (no SSE through the BFF); history persists as `messages` | mock Playwright |
| 8 | Telemetry | `POST /api/v1/telemetry/batch` | `batch_id` idempotent; `captured_at` within [-24 h, +60 s]; GPU rows per index (0 and 1), vLLM counters; connector spools to disk when offline and replays oldest-first | pytest (`test_telemetry`, `test_runner`) |
| 9 | Dashboards | `metrics/gpu`, `metrics/vllm`, `runs/{id}` | rates are derived from counter deltas; first sample, counter reset → `null`, never 0 | pytest `test_series.py` |

## Defects found by this trace (fixed in this session)
1. **Chunk count was sent as `token_count`.** `connector/jobs.py` filled `token_count` with the number of SSE chunks coalesced into an event. A chunk is not a token. The connector now sends `chunk_count`; `token_count` stays reserved for a server-reported exact count (none is reported today). API model + both stores persist `chunk_count`. Test: `test_events_report_chunk_counts_never_token_counts`. (The legacy Postgres/Supabase stores ignore the new field; they are not on the AWS path.)
2. **Notebook did not enforce Python 3.13** although build 5 is a `cp313` wheel; it now asserts it before anything else, and it installs `nvidia-ml-py --no-deps` if the NVML module is missing.
3. **TP=2 had no separate test.** Notebook §9 is a distinct, labelled-UNVERIFIED experiment that requires model memory on **both** GPUs before it may be called tensor-parallel.

## Where "tokens" legitimately appear (and where they must not)
- *Allowed:* vLLM's own cumulative counters (`prompt_tokens_total`, `generation_tokens_total`) and rates derived from their deltas ("from vLLM counter deltas"). They are measurements from the server.
- *Not claimed:* per-reply token counts or tokens/s in the Playground. The Playground shows `stream_chunks`, `chunks_per_s`, TTFT and end-to-end latency; "Output tokens" is shown as unavailable because the streaming API reports no usage. `completion_tokens` is only accepted when a server reports it.

## Known gaps (not fixed here)
- No real-service evidence: DynamoDB tests ran on an emulator; the real-table parity run needs owner approval (`docs/AWS_DYNAMODB_REAL_PARITY.md`).
- `StokastiskLlm` does not request `stream_options.include_usage`, so exact usage is unavailable until the SDK exposes it; this is why tokens stay "unavailable".
