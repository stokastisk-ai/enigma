-- Enigma core schema, PORTABLE PostgreSQL (Neon / local)..
-- Design notes:
--   * no RLS policies, no auth.jwt(), no realtime.* : authorization is enforced by the FastAPI layer (verified Neon Auth identity ->
--     membership -> explicit workspace/runtime ids), and the SQL functions in 0002 re-check those ids against the rows.
--     Browsers NEVER connect to the database, so there is no browser-facing policy surface to protect.
--   * no pgcrypto: gen_random_uuid() and sha256() are built into PostgreSQL 13+/11+.
--   * identity column `user_id`/`created_by`/`actor` holds the provider-namespaced subject (e.g. 'neon:<sub>').
--   * one least-privilege application role, enigma_app (NOLOGIN group role; grant it to the real login role out of band).
-- Apply to a DEDICATED Enigma database only. Never to another project's database.

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'enigma_app') then create role enigma_app nologin nosuperuser nocreatedb nocreaterole nobypassrls; end if;
end $$;

-- ---------------------------------------------------------------- tenancy
create table public.workspaces (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(name) between 1 and 80),
  slug text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{1,38}$'),
  created_by text not null,
  created_at timestamptz not null default now()
);

create table public.memberships (
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id text not null,
  role text not null check (role in ('owner','admin','member','viewer')),
  created_at timestamptz not null default now(),
  primary key (workspace_id, user_id)
);
create index memberships_user_idx on public.memberships (user_id);

create table public.projects (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 80),
  created_by text not null,
  created_at timestamptz not null default now(),
  archived_at timestamptz,
  unique (id, workspace_id)
);
create index projects_ws_idx on public.projects (workspace_id, created_at desc);

create table public.models (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  hf_id text not null check (char_length(hf_id) between 1 and 200),
  label text,
  -- evidence label copied from the SDK's check_model(); never "verified" without a recorded run
  evidence text not null default 'unknown_requires_evidence'
    check (evidence in ('kaggle_tested','experimental','unknown_requires_evidence','mock')),
  max_model_len int check (max_model_len is null or max_model_len between 128 and 131072),
  tp int not null default 1 check (tp between 1 and 8),
  pp int not null default 1 check (pp between 1 and 8),
  created_at timestamptz not null default now(),
  unique (workspace_id, hf_id, tp, pp)
);

-- ---------------------------------------------------------------- runtimes + credentials
create table public.runtimes (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  name text not null check (char_length(name) between 1 and 80),
  is_mock boolean not null default false,          -- mock connectors are always labelled
  served_model text,
  tp int,
  pp int,
  max_model_len int,
  capabilities jsonb not null default '{}'::jsonb,  -- gpu inventory, versions, supported counters
  binary_identity jsonb not null default '{}'::jsonb, -- sdk/vllm versions, wheel sha as reported (unverified claim)
  last_heartbeat_at timestamptz,
  registered_at timestamptz,
  disabled_at timestamptz,
  created_by text not null,
  created_at timestamptz not null default now(),
  foreign key (project_id, workspace_id) references public.projects (id, workspace_id) on delete cascade,
  unique (id, workspace_id)
);
create index runtimes_hb_idx on public.runtimes (id, last_heartbeat_at);
create index runtimes_ws_idx on public.runtimes (workspace_id, project_id);

create table public.runtime_credentials (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  runtime_id uuid not null,
  prefix text not null unique check (prefix ~ '^[a-z0-9]{8}$'),
  secret_hash text not null check (secret_hash ~ '^[0-9a-f]{64}$'), -- sha256 of a 256-bit random secret
  scopes text[] not null default '{runtime:connect}',
  created_by text not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  revoked_at timestamptz,
  rotated_from uuid references public.runtime_credentials(id),
  last_used_at timestamptz,
  foreign key (runtime_id, workspace_id) references public.runtimes (id, workspace_id) on delete cascade
);
create index runtime_credentials_runtime_idx on public.runtime_credentials (runtime_id);

-- ---------------------------------------------------------------- runs (one per runtime session / benchmark)
create table public.inference_runs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  runtime_id uuid not null,
  kind text not null default 'session' check (kind in ('session','benchmark')),
  is_mock boolean not null default false,
  model text,
  tp int, pp int, max_model_len int,
  status text not null default 'active' check (status in ('active','ended','aborted')),
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  summary jsonb not null default '{}'::jsonb,
  foreign key (runtime_id, workspace_id) references public.runtimes (id, workspace_id) on delete cascade
);
create index runs_ws_time_idx on public.inference_runs (workspace_id, project_id, started_at desc);
create index runs_runtime_idx on public.inference_runs (runtime_id, started_at desc);

-- ---------------------------------------------------------------- chat
create table public.conversations (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  runtime_id uuid references public.runtimes(id) on delete set null,
  title text not null default 'New conversation' check (char_length(title) <= 120),
  created_by text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (project_id, workspace_id) references public.projects (id, workspace_id) on delete cascade,
  unique (id, workspace_id)
);
create index conversations_idx on public.conversations (workspace_id, project_id, updated_at desc);

create table public.messages (
  id uuid primary key default gen_random_uuid(),
  ordinal bigint generated always as identity,
  workspace_id uuid not null,
  conversation_id uuid not null,
  role text not null check (role in ('user','assistant','system')),
  content text not null check (char_length(content) <= 65536),
  job_id uuid,
  meta jsonb not null default '{}'::jsonb,   -- measured diagnostics for assistant messages
  created_at timestamptz not null default now(),
  foreign key (conversation_id, workspace_id) references public.conversations (id, workspace_id) on delete cascade
);
create index messages_conv_idx on public.messages (conversation_id, ordinal);
-- final assistant message stored once per job
create unique index messages_one_assistant_per_job on public.messages (job_id) where role = 'assistant' and job_id is not null;

create table public.inference_jobs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  project_id uuid not null,
  conversation_id uuid not null,
  runtime_id uuid not null,
  requested_by text not null,
  idempotency_key text not null check (char_length(idempotency_key) between 8 and 128),
  status text not null default 'pending'
    check (status in ('pending','claimed','running','succeeded','failed','cancel_requested','cancelled','expired')),
  request jsonb not null,                     -- {messages:[{role,content}], max_tokens, temperature, model}
  attempt int not null default 0,
  max_attempts int not null default 2 check (max_attempts between 1 and 5),
  lease_owner text,
  lease_expires_at timestamptz,
  last_event_seq int not null default 0,
  event_count int not null default 0,
  text_chars int not null default 0,
  created_at timestamptz not null default now(),
  deadline_at timestamptz not null default (now() + interval '10 minutes'),
  claimed_at timestamptz,
  first_token_at timestamptz,
  finished_at timestamptz,
  cancel_requested_at timestamptz,
  error_code text,
  error_message text check (error_message is null or char_length(error_message) <= 500),
  result jsonb,                               -- measured by the connector: ttft_ms, e2e_ms, token counts
  assistant_message_id uuid,
  foreign key (conversation_id, workspace_id) references public.conversations (id, workspace_id) on delete cascade,
  foreign key (runtime_id, workspace_id) references public.runtimes (id, workspace_id) on delete cascade,
  unique (workspace_id, idempotency_key)
);
create index jobs_claim_idx on public.inference_jobs (runtime_id, status, created_at);
create index jobs_conv_idx on public.inference_jobs (conversation_id, created_at desc);

create table public.inference_events (
  job_id uuid not null references public.inference_jobs(id) on delete cascade,
  event_seq int not null check (event_seq >= 1),
  workspace_id uuid not null,
  kind text not null check (kind in ('delta','reset','status')),
  text text check (text is null or char_length(text) <= 8192),
  token_count int check (token_count is null or token_count >= 0),
  emitted_at timestamptz,
  received_at timestamptz not null default now(),
  primary key (job_id, event_seq)
);
create index events_ws_idx on public.inference_events (workspace_id, received_at);

-- ---------------------------------------------------------------- telemetry (bounded, sampled; never one row per token)
create table public.gpu_samples (
  id bigint generated always as identity primary key,
  workspace_id uuid not null,
  project_id uuid not null,
  runtime_id uuid not null,
  run_id uuid,
  captured_at timestamptz not null,
  gpu_index smallint not null check (gpu_index between 0 and 15),
  gpu_uuid text,
  util_gpu_pct real check (util_gpu_pct is null or util_gpu_pct between 0 and 100),
  util_mem_pct real check (util_mem_pct is null or util_mem_pct between 0 and 100),
  mem_used_mib real check (mem_used_mib is null or mem_used_mib >= 0),
  mem_total_mib real check (mem_total_mib is null or mem_total_mib >= 0),
  temp_c real,
  power_w real check (power_w is null or power_w >= 0),
  power_limit_w real,
  sm_clock_mhz real,
  mem_clock_mhz real,
  pcie_rx_kib_s real,
  pcie_tx_kib_s real,
  is_mock boolean not null default false,
  foreign key (runtime_id, workspace_id) references public.runtimes (id, workspace_id) on delete cascade,
  unique (runtime_id, gpu_index, captured_at)
);
create index gpu_samples_ws_idx on public.gpu_samples (workspace_id, project_id, captured_at desc);
create index gpu_samples_rt_idx on public.gpu_samples (runtime_id, captured_at desc);

create table public.vllm_metric_samples (
  id bigint generated always as identity primary key,
  workspace_id uuid not null,
  project_id uuid not null,
  runtime_id uuid not null,
  run_id uuid,
  captured_at timestamptz not null,
  counter_epoch int not null default 0,       -- increments when the connector sees a counter reset (vLLM restart)
  num_requests_running real,
  num_requests_waiting real,
  kv_cache_usage_perc real check (kv_cache_usage_perc is null or kv_cache_usage_perc between 0 and 1.0000001),
  prompt_tokens_total double precision,
  generation_tokens_total double precision,
  preemptions_total double precision,
  request_success jsonb,                      -- {finished_reason: cumulative_count}
  ttft_hist jsonb,                            -- {"le":[..],"cum":[..],"sum":x,"count":n} cumulative, seconds
  e2e_hist jsonb,
  itl_hist jsonb,
  is_mock boolean not null default false,
  foreign key (runtime_id, workspace_id) references public.runtimes (id, workspace_id) on delete cascade,
  unique (runtime_id, captured_at)
);
create index vllm_samples_ws_idx on public.vllm_metric_samples (workspace_id, project_id, captured_at desc);
create index vllm_samples_rt_idx on public.vllm_metric_samples (runtime_id, captured_at desc);

create table public.ingest_batches (
  runtime_id uuid not null references public.runtimes(id) on delete cascade,
  batch_id text not null check (char_length(batch_id) between 8 and 80),
  received_at timestamptz not null default now(),
  primary key (runtime_id, batch_id)
);

create table public.audit_events (
  id bigint generated always as identity primary key,
  workspace_id uuid,
  actor text not null,            -- user id, or 'runtime:<id>'
  action text not null,
  target_type text,
  target_id text,
  meta jsonb not null default '{}'::jsonb,   -- never contains prompts or secrets
  at timestamptz not null default now()
);
create index audit_ws_idx on public.audit_events (workspace_id, at desc);

-- ---------------------------------------------------------------- privileges (least privilege)
revoke all on schema public from public;
grant usage on schema public to enigma_app;
revoke all on all tables in schema public from public;
grant select, insert, update, delete on all tables in schema public to enigma_app;
-- audit trail is append-only, even for the application role
revoke update, delete, truncate on public.audit_events from public, enigma_app;
grant usage, select on all sequences in schema public to enigma_app;
