-- Kaggle diagnostic markers (replaces the AWS diag-ingest Lambda + DynamoDB table). Idempotent.
-- Separate credential type from runtime tokens: `edx_<id>_<secret>` keys, sha256(secret) stored, per-key event quota + expiry + revocation.
-- Same VM and database as the app (acknowledged shared failure domain); logically separate tables, keys and route (/api/v1/diag/events).
create table if not exists public.diag_keys (
  id text primary key check (id ~ '^[a-z0-9]{8}$'),
  secret_hash text not null check (secret_hash ~ '^[0-9a-f]{64}$'),
  label text not null check (char_length(label) between 1 and 80),
  max_events int not null check (max_events between 1 and 100000),
  used int not null default 0 check (used >= 0 and used <= max_events),
  expires_at timestamptz not null,
  revoked boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.diag_events (
  attempt text not null check (attempt ~ '^[A-Za-z0-9._:-]{1,80}$'),
  seq int not null check (seq >= 0 and seq < 100000000),
  key_id text not null references public.diag_keys(id),
  nb text not null,
  marker text not null,
  utc text not null,
  payload jsonb not null,
  received_at timestamptz not null default now(),
  primary key (attempt, seq)
);
create index if not exists diag_events_received_idx on public.diag_events (received_at);

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'enigma_app') then
    grant select, insert, update on public.diag_keys to enigma_app;     -- update: quota counter only (the API never deletes keys)
    grant select, insert, delete on public.diag_events to enigma_app;   -- delete: retention prune only
    -- 0005 grants all DML on every public table on each bootstrap run; narrow it again here (0006 always runs after 0005).
    revoke delete, truncate on public.diag_keys from enigma_app;
    revoke update, truncate on public.diag_events from enigma_app;
  end if;
end $$;
