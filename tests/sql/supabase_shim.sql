-- TEST-ONLY shim: recreates the minimal Supabase surface the migrations rely on, on a vanilla Postgres.
-- Real Supabase provides all of this; this file must never be applied to a Supabase project.
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to service_role;
alter default privileges in schema public grant all on sequences to service_role;

create schema auth;
grant usage on schema auth to anon, authenticated, service_role;
create function auth.jwt() returns jsonb language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
$$;

create schema realtime;
grant usage on schema realtime to authenticated, service_role;
create table realtime.messages (id bigserial primary key, topic text not null, extension text not null, payload jsonb);
alter table realtime.messages enable row level security;
grant select on realtime.messages to authenticated;
create function realtime.topic() returns text language sql stable as $$
  select nullif(current_setting('realtime.topic', true), '')
$$;
grant execute on function realtime.topic() to authenticated;

-- Supabase installs pgcrypto in the `extensions` schema
create schema if not exists extensions;
