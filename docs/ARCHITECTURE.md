# Architecture

```
Browser ──httpOnly Better Auth session cookie──> Caddy (TLS) ──> Next.js
   │  /api/enigma/*  = BFF: verifies the session, mints a 15-min EdDSA token, allow-lists method+path, zod-validates bodies
   v
FastAPI (private network; tenant routes are not public; runtime plane /api/v1/{runtimes,telemetry,jobs,diag} via Caddy)
   │  private network, least-privilege database role, TLS 1.3 with hostname verification
   v
PostgreSQL 18  (jobs, events, messages, telemetry, auth schema; atomic SQL functions)
   ^
   │  outbound HTTPS: claim / events / complete / telemetry / heartbeat / diagnostic markers
GPU host (Kaggle notebook): enigma_connector ── localhost ──> stokastisk ServerProcess (vLLM) + NVML
```
Deployment target: one OCI Always Free A1 VM (`deploy/oci/`). The web tier's auth role can read only the `auth` schema; the API role cannot read it.

## Data plane vs control plane
The GPU host runs vLLM on `127.0.0.1` and polls Enigma. Nothing is exposed, tunnelled or SSE-streamed from Kaggle. Every API request terminates quickly; durable state (jobs, leases, events) lives in Postgres, so no function needs to stay alive.

## Chat protocol
1. Browser POSTs a prompt (`Idempotency-Key` required) -> `enigma_submit_job` stores the user message and a pending job holding a bounded history snapshot.
2. Connector `POST /jobs/claim` -> job leased (30 s, renewed by every upload). Expired lease + attempts left -> re-claimable; none left -> `failed (lease_expired)`.
3. Connector streams via `stokastisk.serving.stream_chat`, batching deltas (first chunk immediately, then ~150 ms) into events with monotonic `event_seq`. Events stay in memory until acknowledged.
4. Server: duplicate seq ignored, gap -> 409 `sequence_gap` + `expected_seq`, out-of-order batch sorted. After each accepted batch it broadcasts to the private channel; durable events allow polling `GET /jobs/{id}?after_seq=`.
5. Completion carries `final_seq` and SHA-256 of the full text; the server recomputes both from stored events and inserts the assistant message exactly once (unique index per job). Retried attempts start with a `reset` event.
6. Cancel: pending -> immediate; running -> `cancel_requested`, surfaced in the next events response (keepalive every 5 s covers long prefill); connector closes the SSE connection (vLLM aborts), uploads the partial text, `ack-cancel`.

## Telemetry
`NvmlSampler` (nvidia-ml-py): per-GPU util, VRAM, temp, power, clocks, PCIe; unsupported -> `NULL`, never polled again. `VllmSampler` scrapes local `/metrics`, parses with `prometheus_client.parser`, keeps an allowlist (names verified against vLLM 0.25.1 `loggers.py`), aggregates engines, keeps histograms cumulative, bumps `counter_epoch` on any counter reset. Batches (<=600 GPU / 120 vLLM rows) go every 5 s with a batch id; failures go to a byte-bounded on-disk spool replayed oldest-first. The API derives rates and Prometheus-style quantiles from consecutive samples **within an epoch** only.

## Provenance
Runtimes are issued as real or mock and the credential is bound to that: a mock connector cannot register on a real credential (`mock_mismatch`). Rows carry `is_mock`; the UI shows Synthetic / Measured / Mixed everywhere.
