-- Enigma core schema. Identity = Clerk user id (JWT `sub`) via Supabase third-party auth.
-- Privileged writes happen only through FastAPI (service_role). Browsers get SELECT under RLS.

create extension if not exists pgcrypto;

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
  actor text not null,            -- clerk user id, or 'runtime:<id>'
  action text not null,
  target_type text,
  target_id text,
  meta jsonb not null default '{}'::jsonb,   -- never contains prompts or secrets
  at timestamptz not null default now()
);
create index audit_ws_idx on public.audit_events (workspace_id, at desc);

-- ---------------------------------------------------------------- RLS helpers
create or replace function public.enigma_is_member(ws uuid, min_role text default 'viewer')
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.memberships m
    where m.workspace_id = ws
      and m.user_id = (select auth.jwt() ->> 'sub')
      and (case m.role when 'owner' then 4 when 'admin' then 3 when 'member' then 2 else 1 end)
        >= (case min_role when 'owner' then 4 when 'admin' then 3 when 'member' then 2 else 1 end)
  );
$$;

create or replace function public.enigma_safe_uuid(t text)
returns uuid language plpgsql immutable as $$
begin return t::uuid; exception when others then return null; end $$;

-- ---------------------------------------------------------------- RLS: private by default
alter table public.workspaces enable row level security;
alter table public.memberships enable row level security;
alter table public.projects enable row level security;
alter table public.models enable row level security;
alter table public.runtimes enable row level security;
alter table public.runtime_credentials enable row level security;
alter table public.inference_runs enable row level security;
alter table public.conversations enable row level security;
alter table public.messages enable row level security;
alter table public.inference_jobs enable row level security;
alter table public.inference_events enable row level security;
alter table public.gpu_samples enable row level security;
alter table public.vllm_metric_samples enable row level security;
alter table public.ingest_batches enable row level security;
alter table public.audit_events enable row level security;

create policy ws_select on public.workspaces for select to authenticated using (public.enigma_is_member(id));
create policy mem_select on public.memberships for select to authenticated
  using (user_id = (select auth.jwt() ->> 'sub') or public.enigma_is_member(workspace_id, 'admin'));
create policy proj_select on public.projects for select to authenticated using (public.enigma_is_member(workspace_id));
create policy models_select on public.models for select to authenticated using (public.enigma_is_member(workspace_id));
create policy rt_select on public.runtimes for select to authenticated using (public.enigma_is_member(workspace_id));
create policy cred_select on public.runtime_credentials for select to authenticated using (public.enigma_is_member(workspace_id, 'admin'));
create policy runs_select on public.inference_runs for select to authenticated using (public.enigma_is_member(workspace_id));
create policy conv_select on public.conversations for select to authenticated using (public.enigma_is_member(workspace_id));
create policy msg_select on public.messages for select to authenticated using (public.enigma_is_member(workspace_id));
create policy jobs_select on public.inference_jobs for select to authenticated using (public.enigma_is_member(workspace_id));
create policy events_select on public.inference_events for select to authenticated using (public.enigma_is_member(workspace_id));
create policy gpu_select on public.gpu_samples for select to authenticated using (public.enigma_is_member(workspace_id));
create policy vllm_select on public.vllm_metric_samples for select to authenticated using (public.enigma_is_member(workspace_id));
create policy audit_select on public.audit_events for select to authenticated using (workspace_id is not null and public.enigma_is_member(workspace_id, 'admin'));
-- ingest_batches: no policy => no access for browsers.

-- Table privileges: browsers read only. Never grant anything to anon.
revoke all on all tables in schema public from anon, authenticated;
grant select on public.workspaces, public.memberships, public.projects, public.models, public.runtimes,
  public.inference_runs, public.conversations, public.messages, public.inference_jobs, public.inference_events,
  public.gpu_samples, public.vllm_metric_samples, public.audit_events to authenticated;
-- credentials: metadata only, never secret_hash
grant select (id, workspace_id, project_id, runtime_id, prefix, scopes, created_by, created_at, expires_at, revoked_at, last_used_at)
  on public.runtime_credentials to authenticated;
-- the job request holds the prompt snapshot; members of the workspace may read it (they can read the messages anyway)

-- audit trail is append-only, even for service_role
revoke update, delete, truncate on public.audit_events from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------- Realtime private channel authorisation
-- Topic format: conv:<conversation_id>. Requires Realtime "private channels" (RLS on realtime.messages).
create policy enigma_realtime_receive on realtime.messages for select to authenticated
  using (
    realtime.messages.extension = 'broadcast'
    and exists (
      select 1 from public.conversations c
      where c.id = public.enigma_safe_uuid(split_part((select realtime.topic()), ':', 2))
        and split_part((select realtime.topic()), ':', 1) = 'conv'
        and public.enigma_is_member(c.workspace_id)
    )
  );
-- No INSERT policy: browsers cannot publish. Only the FastAPI service role broadcasts.
