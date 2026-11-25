export type Workspace = { id: string; name: string; slug: string; role: "owner" | "admin" | "member" | "viewer" };
export type Project = { id: string; workspace_id: string; name: string };
export type RuntimeStatus = "online" | "stale" | "offline" | "never_connected";
export type Runtime = {
  id: string; name: string; project_id: string; is_mock: boolean; served_model: string | null; tp: number | null; pp: number | null;
  max_model_len: number | null; capabilities: { gpus?: { index: number; uuid: string | null; name: string | null; memory_total_mib: number | null }[] } & Record<string, unknown>;
  binary_identity_reported: Record<string, string | null>; last_heartbeat_at: string | null; status: RuntimeStatus; heartbeat_age_s: number | null;
};
export type Conversation = { id: string; title: string; runtime_id: string | null; updated_at: string };
export type Measured = { ttft_ms?: number; e2e_ms?: number; stream_chunks?: number; chunks_per_s?: number; completion_tokens?: number; model?: string; tp?: number; pp?: number; max_model_len?: number; is_mock?: boolean };
export type Message = { id: string; role: "user" | "assistant" | "system"; content: string; job_id: string | null; created_at: string; meta: { measured?: Measured; cancelled?: boolean } };
export type JobStatus = "pending" | "claimed" | "running" | "succeeded" | "failed" | "cancel_requested" | "cancelled" | "expired";
export type JobEvent = { seq: number; kind: "delta" | "reset" | "status"; text: string | null };
export type JobView = { job_id: string; status: JobStatus; conversation_id: string; runtime_id: string; attempt: number; error_code: string | null; error_message: string | null; result: (Measured & { text_sha256?: string }) | null; assistant_message_id: string | null; last_event_seq: number; events: JobEvent[]; runtime_status: RuntimeStatus | null };
export const TERMINAL: JobStatus[] = ["succeeded", "failed", "cancelled", "expired"];

export type Provenance = { rows: number; mock_rows: number; measured_rows: number };
export type GpuPoint = { t: string; samples: number; gpu_uuid: string | null } & Record<"util_gpu_pct" | "util_mem_pct" | "mem_used_mib" | "mem_total_mib" | "temp_c" | "power_w" | "power_limit_w" | "sm_clock_mhz" | "mem_clock_mhz" | "pcie_rx_kib_s" | "pcie_tx_kib_s", number | null>;
export type GpuLatest = Omit<GpuPoint, "t" | "samples"> & { captured_at: string; age_s: number; is_mock: boolean };
export type GpuMetrics = { provenance: Provenance; truncated: boolean; series: Record<string, GpuPoint[]>; latest: Record<string, GpuLatest> };
export type VllmPoint = { t: string; num_requests_running: number | null; num_requests_waiting: number | null; kv_cache_usage_perc: number | null; prompt_tokens_per_s: number | null; generation_tokens_per_s: number | null; preemptions_in_interval: number | null; ttft_p50_s: number | null; ttft_p95_s: number | null; e2e_p50_s: number | null; e2e_p95_s: number | null; itl_p50_s: number | null; itl_p95_s: number | null; requests_in_interval: number | null; counter_reset: boolean };
export type Hist = { le: number[]; cum: number[]; sum: number; count: number };
export type VllmMetrics = { provenance: Provenance; truncated: boolean; points: VllmPoint[]; latest_age_s: number | null; latest_cumulative: { ttft: Hist | null; e2e: Hist | null; itl: Hist | null; request_success: Record<string, number> | null } | null };
export type Overview = { runtimes: Runtime[]; provenance: Provenance; gpus: (GpuLatest & { runtime_id: string; gpu_index: number })[]; vllm_points: VllmPoint[] };
export type Run = { id: string; runtime_id: string; kind: string; is_mock: boolean; model: string | null; tp: number | null; pp: number | null; max_model_len: number | null; status: "active" | "ended" | "aborted"; started_at: string; ended_at: string | null };
export type RunSummary = { run: Run; provenance: Provenance; summary: { gpus: Record<string, { util_gpu_avg_pct: number | null; util_gpu_max_pct: number | null; mem_used_max_mib: number | null; mem_total_mib: number | null; temp_max_c: number | null; power_avg_w: number | null; power_max_w: number | null; samples: number }>; ttft_p50_s: number | null; ttft_p95_s: number | null; e2e_p50_s: number | null; e2e_p95_s: number | null; prompt_tokens: number | null; generation_tokens: number | null; generation_tokens_per_s_avg: number | null; kv_cache_max_perc: number | null; preemptions: number | null } };
export type Credential = { id: string; prefix: string; created_at: string; expires_at: string; revoked_at: string | null; last_used_at: string | null };
export type RuntimeToken = { runtime_id: string; credential_id: string; token: string; prefix: string; expires_at: string };
