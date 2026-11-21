-- Enigma: two-tenant RLS + Realtime-policy verification for a HOSTED Supabase project.
-- Paste into the Supabase SQL editor of the dedicated Enigma project (NEVER another project) AFTER applying migrations 0001 and 0002.
-- Everything runs inside ONE transaction that ends in ROLLBACK, so no fixture row, helper function or setting survives.
-- If the script finishes without an error, every check passed. Any failure raises "FAILED: <check name>".
begin;

-- Run by tests/sql/run_sql_tests.py on a throwaway Postgres. Each check raises on failure.
create table zz_results(name text, ok boolean);
grant all on zz_results to public;
create function zz_check(name text, cond boolean) returns void language plpgsql as $$
begin
  if cond is not true then raise exception 'FAILED: %', name; end if;
  insert into zz_results values (name, true);
end $$;
create function zz_raises(name text, stmt text) returns void language plpgsql as $$
begin
  begin execute stmt; exception when others then insert into zz_results values (name, true); return; end;
  raise exception 'FAILED (expected error): %', name;
end $$;
create function zz_claims(sub text) returns void language sql as $$
  select set_config('request.jwt.claims', jsonb_build_object('sub', sub)::text, true)
$$;
grant execute on function zz_check(text, boolean), zz_raises(text, text), zz_claims(text) to public;

-- ------------------------------------------------------------------ fixtures (as service_role)
set role service_role;
insert into workspaces (id, name, slug, created_by) values
  ('a0000000-0000-0000-0000-000000000001','Alpha','alpha','user_A'),
  ('b0000000-0000-0000-0000-000000000001','Beta','beta','user_B');
insert into memberships values
  ('a0000000-0000-0000-0000-000000000001','user_A','owner'),
  ('a0000000-0000-0000-0000-000000000001','user_viewer','viewer'),
  ('b0000000-0000-0000-0000-000000000001','user_B','owner');
insert into projects (id, workspace_id, name, created_by) values
  ('a1000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000001','pa','user_A'),
  ('b1000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000001','pb','user_B');
insert into runtimes (id, workspace_id, project_id, name, is_mock, served_model, max_model_len, created_by) values
  ('a2000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000001','a1000000-0000-0000-0000-000000000001','rt-a',true,'mock-model',2048,'user_A'),
  ('a2000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000001','a1000000-0000-0000-0000-000000000001','rt-a2',true,'mock-model',2048,'user_A'),
  ('b2000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000001','b1000000-0000-0000-0000-000000000001','rt-b',true,'mock-model',2048,'user_B');
insert into runtime_credentials (workspace_id, project_id, runtime_id, prefix, secret_hash, created_by, expires_at) values
  ('a0000000-0000-0000-0000-000000000001','a1000000-0000-0000-0000-000000000001','a2000000-0000-0000-0000-000000000001','abcd1234',repeat('a',64),'user_A', now()+interval '1 day'),
  ('b0000000-0000-0000-0000-000000000001','b1000000-0000-0000-0000-000000000001','b2000000-0000-0000-0000-000000000001','wxyz5678',repeat('b',64),'user_B', now()+interval '1 day');
insert into conversations (id, workspace_id, project_id, title, created_by) values
  ('a3000000-0000-0000-0000-000000000001','a0000000-0000-0000-0000-000000000001','a1000000-0000-0000-0000-000000000001','ca','user_A'),
  ('b3000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000001','b1000000-0000-0000-0000-000000000001','cb','user_B');
insert into messages (workspace_id, conversation_id, role, content) values
  ('a0000000-0000-0000-0000-000000000001','a3000000-0000-0000-0000-000000000001','user','secret prompt A'),
  ('b0000000-0000-0000-0000-000000000001','b3000000-0000-0000-0000-000000000001','user','secret prompt B');
insert into gpu_samples (workspace_id, project_id, runtime_id, captured_at, gpu_index, util_gpu_pct) values
  ('a0000000-0000-0000-0000-000000000001','a1000000-0000-0000-0000-000000000001','a2000000-0000-0000-0000-000000000001', now(), 0, 10),
  ('b0000000-0000-0000-0000-000000000001','b1000000-0000-0000-0000-000000000001','b2000000-0000-0000-0000-000000000001', now(), 0, 20);
reset role;

-- ------------------------------------------------------------------ RLS: tenant isolation
select zz_claims('user_A'); set role authenticated;
select zz_check('A sees only own workspace', (select count(*) from workspaces) = 1 and (select slug from workspaces) = 'alpha');
select zz_check('A sees only own messages', (select count(*) from messages) = 1 and (select content from messages) = 'secret prompt A');
select zz_check('A sees only own gpu samples', (select count(*) from gpu_samples) = 1);
select zz_check('A sees only own runtimes', (select count(*) from runtimes) = 2);
select zz_check('A sees only own conversations', (select count(*) from conversations) = 1);
select zz_check('A (owner) sees credential metadata', (select count(*) from runtime_credentials) = 1);
select zz_raises('A cannot read secret_hash', 'select secret_hash from runtime_credentials');
select zz_raises('A cannot insert', $$insert into projects (workspace_id, name, created_by) values ('a0000000-0000-0000-0000-000000000001','x','user_A')$$);
select zz_raises('A cannot insert into other tenant', $$insert into messages (workspace_id, conversation_id, role, content) values ('b0000000-0000-0000-0000-000000000001','b3000000-0000-0000-0000-000000000001','user','x')$$);
select zz_raises('A cannot update', $$update messages set content = 'hacked'$$);
select zz_raises('A cannot delete', $$delete from conversations$$);
select zz_raises('A cannot call service function', $$select enigma_claim_job('a2000000-0000-0000-0000-000000000001','x')$$);
select zz_raises('A cannot read ingest_batches', 'select * from ingest_batches');
select zz_raises('A cannot read audit as non-owner? (audit allowed for admin only) insert denied', $$insert into audit_events (actor, action) values ('a','b')$$);
reset role;

select zz_claims('user_B'); set role authenticated;
select zz_check('B sees only own messages', (select count(*) from messages) = 1 and (select content from messages) = 'secret prompt B');
reset role;

select zz_claims('user_viewer'); set role authenticated;
select zz_check('viewer reads workspace data', (select count(*) from conversations) = 1);
select zz_check('viewer cannot see credential metadata', (select count(*) from runtime_credentials) = 0);
reset role;

select zz_claims('user_stranger'); set role authenticated;
select zz_check('non-member sees nothing', (select count(*) from workspaces) = 0 and (select count(*) from messages) = 0
  and (select count(*) from runtimes) = 0 and (select count(*) from gpu_samples) = 0);
reset role;

-- a JWT with no sub claim sees nothing
select set_config('request.jwt.claims', '{}', true);
set role authenticated;
select zz_check('missing sub sees nothing', (select count(*) from workspaces) = 0);
reset role;

-- anon gets nothing at all
set role anon;
select zz_raises('anon cannot select', 'select * from workspaces');
reset role;

-- ------------------------------------------------------------------ Realtime private channel authorisation
insert into realtime.messages (topic, extension) values
  ('conv:a3000000-0000-0000-0000-000000000001','broadcast'),
  ('conv:b3000000-0000-0000-0000-000000000001','broadcast'),
  ('conv:not-a-uuid','broadcast');
select zz_claims('user_A'); set role authenticated;
select set_config('realtime.topic', 'conv:a3000000-0000-0000-0000-000000000001', false);
select zz_check('A may receive own conversation topic', (select count(*) from realtime.messages where topic = realtime.topic()) = 1);
select set_config('realtime.topic', 'conv:b3000000-0000-0000-0000-000000000001', false);
select zz_check('A may NOT receive tenant B topic', (select count(*) from realtime.messages where topic = realtime.topic()) = 0);
select set_config('realtime.topic', 'conv:not-a-uuid', false);
select zz_check('malformed topic denied without error', (select count(*) from realtime.messages where topic = realtime.topic()) = 0);
select set_config('realtime.topic', 'other:a3000000-0000-0000-0000-000000000001', false);
select zz_check('wrong topic prefix denied', (select count(*) from realtime.messages where topic = realtime.topic()) = 0);
select zz_raises('browser cannot publish to realtime', $$insert into realtime.messages (topic, extension) values ('conv:a3000000-0000-0000-0000-000000000001','broadcast')$$);
reset role;

do $$ begin raise notice 'Enigma RLS verification: all % checks passed (transaction will be rolled back)', (select count(*) from zz_results); end $$;
rollback;
