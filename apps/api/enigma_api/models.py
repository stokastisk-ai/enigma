"""Request/response schemas. Everything has explicit bounds: no unbounded list, string or number reaches the store."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Pct = Annotated[float, Field(ge=0, le=100)]
Short = Annotated[str, Field(min_length=1, max_length=80)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


# ================================================================= runtime plane (SDK / connector)
class GpuInfo(Strict):
    index: int = Field(ge=0, le=15)
    uuid: str | None = Field(default=None, max_length=80)
    name: str | None = Field(default=None, max_length=80)
    memory_total_mib: float | None = Field(default=None, ge=0, le=1_000_000)


class SdkIdentity(Strict):
    """Self-reported by the connector. Informational only; never used for authorisation."""
    stokastisk_version: str | None = Field(default=None, max_length=40)
    stokastisk_build: str | None = Field(default=None, max_length=40)
    vllm_version: str | None = Field(default=None, max_length=40)
    wheel_sha256: Sha256Hex | None = None
    python_version: str | None = Field(default=None, max_length=40)
    driver_version: str | None = Field(default=None, max_length=40)


class RegisterRequest(Strict):
    connector_version: str = Field(max_length=40)
    is_mock: bool
    served_model: str = Field(min_length=1, max_length=200)
    tp: int = Field(default=1, ge=1, le=8)
    pp: int = Field(default=1, ge=1, le=8)
    max_model_len: int | None = Field(default=None, ge=128, le=131072)
    gpus: list[GpuInfo] = Field(default_factory=list, max_length=16)
    sdk: SdkIdentity = Field(default_factory=SdkIdentity)
    capabilities: dict[str, bool] = Field(default_factory=dict, max_length=32)


class Limits(BaseModel):
    max_events_per_request: int = 64
    max_event_text_chars: int = 8192
    max_gpu_rows_per_batch: int = 600
    max_vllm_rows_per_batch: int = 120
    max_job_text_chars: int = 60000
    max_job_events: int = 5000
    max_body_bytes: int = 256 * 1024


class RegisterResponse(BaseModel):
    runtime_id: str
    run_id: str
    heartbeat_interval_s: int = 10
    claim_poll_interval_s: float = 1.0
    limits: Limits = Field(default_factory=Limits)


class HeartbeatResponse(BaseModel):
    ok: bool = True
    server_time: datetime


class HistIn(Strict):
    """Cumulative Prometheus histogram. ``le`` = finite upper bounds (ascending); ``cum`` = cumulative counts incl. the +Inf bucket."""
    le: list[float] = Field(max_length=64)
    cum: list[float] = Field(max_length=65)
    sum: float = Field(ge=0)
    count: float = Field(ge=0)

    @model_validator(mode="after")
    def _shape(self):
        if len(self.cum) != len(self.le) + 1:
            raise ValueError("cum must have len(le)+1 entries (last is the +Inf bucket)")
        if any(b >= a for a, b in zip(self.le[1:], self.le)):
            raise ValueError("le must be strictly ascending")
        if any(b < a for a, b in zip(self.cum, self.cum[1:])):
            raise ValueError("cumulative counts must be non-decreasing")
        return self


def _utc(v: datetime) -> datetime:
    """Naive timestamps are read as UTC. The acceptable window (not in the future, not older than 24 h) is enforced in the route with
    the application clock, so there is a single source of 'now'."""
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


class GpuSampleIn(Strict):
    captured_at: datetime
    gpu_index: int = Field(ge=0, le=15)
    gpu_uuid: str | None = Field(default=None, max_length=80)
    # null = sensor unavailable. Missing is never coerced to 0.
    util_gpu_pct: Pct | None = None
    util_mem_pct: Pct | None = None
    mem_used_mib: float | None = Field(default=None, ge=0, le=1_000_000)
    mem_total_mib: float | None = Field(default=None, ge=0, le=1_000_000)
    temp_c: float | None = Field(default=None, ge=-50, le=200)
    power_w: float | None = Field(default=None, ge=0, le=2000)
    power_limit_w: float | None = Field(default=None, ge=0, le=2000)
    sm_clock_mhz: float | None = Field(default=None, ge=0, le=10000)
    mem_clock_mhz: float | None = Field(default=None, ge=0, le=20000)
    pcie_rx_kib_s: float | None = Field(default=None, ge=0)
    pcie_tx_kib_s: float | None = Field(default=None, ge=0)

    _v = field_validator("captured_at")(_utc)


class VllmSampleIn(Strict):
    captured_at: datetime
    counter_epoch: int = Field(default=0, ge=0, le=1_000_000)
    num_requests_running: float | None = Field(default=None, ge=0)
    num_requests_waiting: float | None = Field(default=None, ge=0)
    kv_cache_usage_perc: float | None = Field(default=None, ge=0, le=1.0000001)    # vLLM reports a 0..1 fraction
    prompt_tokens_total: float | None = Field(default=None, ge=0)
    generation_tokens_total: float | None = Field(default=None, ge=0)
    preemptions_total: float | None = Field(default=None, ge=0)
    request_success: dict[str, float] | None = Field(default=None, max_length=16)
    ttft_hist: HistIn | None = None
    e2e_hist: HistIn | None = None
    itl_hist: HistIn | None = None

    _v = field_validator("captured_at")(_utc)


class TelemetryBatch(Strict):
    batch_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")   # idempotency key
    run_id: str | None = None
    gpu: list[GpuSampleIn] = Field(default_factory=list, max_length=600)
    vllm: list[VllmSampleIn] = Field(default_factory=list, max_length=120)


class TelemetryResponse(BaseModel):
    ok: bool = True
    duplicate: bool = False
    gpu_rows: int = 0
    vllm_rows: int = 0


class ClaimRequest(Strict):
    lease_seconds: int = Field(default=30, ge=5, le=120)


class JobRequest(BaseModel):
    messages: list[dict[str, str]]
    max_tokens: int
    temperature: float
    model: str | None = None


class ClaimedJob(BaseModel):
    job_id: str
    conversation_id: str
    attempt: int
    lease_expires_at: datetime
    next_event_seq: int          # first event_seq the connector must send (continues across attempts)
    request: JobRequest


class ClaimResponse(BaseModel):
    job: ClaimedJob | None = None
    poll_after_ms: int = 1000


class EventIn(Strict):
    event_seq: int = Field(ge=1, le=1_000_000)
    kind: Literal["delta", "reset", "status"] = "delta"
    text: str | None = Field(default=None, max_length=8192)
    token_count: int | None = Field(default=None, ge=0, le=100000)   # ONLY a server-reported exact token count; connectors must not fill it from chunks
    chunk_count: int | None = Field(default=None, ge=0, le=100000)   # number of SSE content chunks coalesced into this event (NOT tokens)
    emitted_at: datetime | None = None


class EventsRequest(Strict):
    events: list[EventIn] = Field(min_length=1, max_length=64)
    lease_seconds: int = Field(default=30, ge=5, le=120)


class EventsResponse(BaseModel):
    ok: bool = True
    last_event_seq: int
    accepted: int
    duplicates: int
    cancel_requested: bool


class Measured(Strict):
    """Measured by the connector around the real stream. Every field is optional: unknown stays null."""
    ttft_ms: float | None = Field(default=None, ge=0, le=3_600_000)
    e2e_ms: float | None = Field(default=None, ge=0, le=3_600_000)
    stream_chunks: int | None = Field(default=None, ge=0, le=100000)
    completion_tokens: int | None = Field(default=None, ge=0, le=100000)   # only when the server reported usage
    prompt_tokens: int | None = Field(default=None, ge=0, le=1_000_000)
    finish_reason: str | None = Field(default=None, max_length=40)
    chunks_per_s: float | None = Field(default=None, ge=0)
    model: str | None = Field(default=None, max_length=200)
    tp: int | None = Field(default=None, ge=1, le=8)
    pp: int | None = Field(default=None, ge=1, le=8)
    max_model_len: int | None = Field(default=None, ge=1)
    gpus: list[int] | None = Field(default=None, max_length=16)
    is_mock: bool | None = None


class CompleteRequest(Strict):
    final_seq: int = Field(ge=0, le=1_000_000)
    text_sha256: Sha256Hex
    result: Measured = Field(default_factory=Measured)


class FailRequest(Strict):
    code: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_.-]+$")
    message: str = Field(max_length=500)
    retryable: bool = False


class JobAck(BaseModel):
    ok: bool = True
    status: str | None = None
    idempotent: bool = False
    assistant_message_id: str | None = None


class JobStatusRuntime(BaseModel):
    job_id: str
    status: str
    cancel_requested: bool
    last_event_seq: int


# ================================================================= user plane (browser via BFF)
class WorkspaceCreate(Strict):
    name: Short
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,38}$")


class ProjectCreate(Strict):
    name: Short


class RuntimeCreate(Strict):
    name: Short
    is_mock: bool = False
    expires_days: int = Field(default=30, ge=1, le=90)


class RuntimeTokenResponse(BaseModel):
    runtime_id: str
    credential_id: str
    token: str = Field(description="Shown once. Store it in a Kaggle secret; only its hash is kept.")
    prefix: str
    expires_at: datetime


class ConversationCreate(Strict):
    title: str | None = Field(default=None, max_length=120)
    runtime_id: str | None = None


class SubmitJob(Strict):
    runtime_id: str
    content: str = Field(min_length=1, max_length=16000)
    max_tokens: int = Field(default=256, ge=1, le=512)
    temperature: float = Field(default=0.2, ge=0, le=2)


class SubmitResponse(BaseModel):
    job_id: str
    status: str
    idempotent: bool = False
    user_message_id: str | None = None


class JobView(BaseModel):
    job_id: str
    status: str
    conversation_id: str
    runtime_id: str
    attempt: int
    created_at: datetime
    first_token_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    result: dict | None = None
    assistant_message_id: str | None = None
    last_event_seq: int
    events: list[dict] = Field(default_factory=list)
    runtime_status: str | None = None


class Page(BaseModel):
    items: list[dict]
