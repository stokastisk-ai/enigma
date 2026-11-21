\set ON_ERROR_STOP on
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
  select set_config('request.jwt.claims', jsonb_build_object('sub', sub)::text, false)
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
select set_config('request.jwt.claims', '{}', false);
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

-- ------------------------------------------------------------------ job lifecycle (service_role)
set role service_role;
select zz_check('submit: cross-workspace conversation rejected',
  (enigma_submit_job('b0000000-0000-0000-0000-000000000001','a3000000-0000-0000-0000-000000000001','b2000000-0000-0000-0000-000000000001','user_B','idem-cross-01','hi','{"max_tokens":16,"temperature":0}',4000)) ->> 'error' = 'not_found');
select zz_check('submit: runtime from other workspace rejected',
  (enigma_submit_job('a0000000-0000-0000-0000-000000000001','a3000000-0000-0000-0000-000000000001','b2000000-0000-0000-0000-000000000001','user_A','idem-cross-02','hi','{"max_tokens":16,"temperature":0}',4000)) ->> 'error' = 'runtime_not_found');
select zz_check('submit: too-long prompt rejected',
  (enigma_submit_job('a0000000-0000-0000-0000-000000000001','a3000000-0000-0000-0000-000000000001','a2000000-0000-0000-0000-000000000001','user_A','idem-long-001',repeat('x',50),'{"max_tokens":16,"temperature":0}',10)) ->> 'error' = 'prompt_too_long');

create temp table j1 as select enigma_submit_job('a0000000-0000-0000-0000-000000000001','a3000000-0000-0000-0000-000000000001','a2000000-0000-0000-0000-000000000001','user_A','idem-job-0001','hello','{"max_tokens":16,"temperature":0}',4000) as r;
select zz_check('submit ok', (select r->>'ok' from j1) = 'true' and (select r->>'status' from j1) = 'pending');
select zz_check('submit idempotent replay returns same job',
  (enigma_submit_job('a0000000-0000-0000-0000-000000000001','a3000000-0000-0000-0000-000000000001','a2000000-0000-0000-0000-000000000001','user_A','idem-job-0001','hello','{"max_tokens":16,"temperature":0}',4000)) ->> 'job_id' = (select r->>'job_id' from j1));
select zz_check('idempotent replay did not duplicate user message', (select count(*) from messages where conversation_id = 'a3000000-0000-0000-0000-000000000001' and role='user') = 2);
select zz_check('idempotent replay in the same conversation stays ok',
  (enigma_submit_job('a0000000-0000-0000-0000-000000000001','a3000000-0000-0000-0000-000000000001','a2000000-0000-0000-0000-000000000001','user_A','idem-job-0001','hello','{"max_tokens":16,"temperature":0}',4000)) ->> 'ok' = 'true');

-- other runtime of same workspace cannot claim it
select zz_check('wrong runtime cannot claim', (select count(*) from enigma_claim_job('a2000000-0000-0000-0000-000000000002','owner-x',30)) = 0);
select zz_check('foreign-tenant runtime cannot claim', (select count(*) from enigma_claim_job('b2000000-0000-0000-0000-000000000001','owner-x',30)) = 0);
create temp table c1 as select * from enigma_claim_job('a2000000-0000-0000-0000-000000000001','owner-1',30);
select zz_check('claim returns the job', (select count(*) from c1) = 1 and (select attempt from c1) = 1 and (select status from c1) = 'claimed');
select zz_check('claimed job has history snapshot', jsonb_array_length((select request->'messages' from c1)) >= 1);
select zz_check('second claim gets nothing while lease is live', (select count(*) from enigma_claim_job('a2000000-0000-0000-0000-000000000001','owner-2',30)) = 0);

-- events: wrong owner, ordering, dup, gap
select zz_check('events: wrong owner = lease_lost',
  (enigma_append_events('a2000000-0000-0000-0000-000000000001',(select id from c1),'owner-2','[{"event_seq":1,"kind":"delta","text":"x"}]'::jsonb)) ->> 'error' = 'lease_lost');
select zz_check('events: another runtime cannot append',
  (enigma_append_events('a2000000-0000-0000-0000-000000000002',(select id from c1),'owner-1','[{"event_seq":1,"kind":"delta","text":"x"}]'::jsonb)) ->> 'error' = 'not_found');
select zz_check('events: gap rejected with expected_seq',
  (enigma_append_events('a2000000-0000-0000-0000-000000000001',(select id from c1),'owner-1','[{"event_seq":2,"kind":"delta","text":"x"}]'::jsonb)) ->> 'expected_seq' = '1');
create temp table e1 as select enigma_append_events('a2000000-0000-0000-0000-000000000001',(select id from c1),'owner-1',
  '[{"event_seq":2,"kind":"delta","text":"lo"},{"event_seq":1,"kind":"delta","text":"hel"}]'::jsonb) as r;
select zz_check('events: out-of-order batch is sorted and accepted', (select r->>'accepted' from e1) = '2' and (select r->>'last_event_seq' from e1) = '2');
select zz_check('events: duplicate delivery ignored',
  (enigma_append_events('a2000000-0000-0000-0000-000000000001',(select id from c1),'owner-1','[{"event_seq":2,"kind":"delta","text":"lo"}]'::jsonb)) ->> 'duplicates' = '1');
select zz_check('events: job running with first token time', (select status from inference_jobs where id = (select id from c1)) = 'running'
  and (select first_token_at from inference_jobs where id = (select id from c1)) is not null);

-- complete: bad hash, bad seq, good, idempotent, conflicting
select zz_check('complete: seq mismatch',
  (enigma_complete_job('a2000000-0000-0000-0000-000000000001',(select id from c1),'owner-1',5,'00','{}'::jsonb)) ->> 'error' = 'seq_mismatch');
select zz_check('complete: hash mismatch',
  (enigma_complete_job('a2000000-0000-0000-0000-000000000001',(select id from c1),'owner-1',2,repeat('0',64),'{}'::jsonb)) ->> 'error' = 'hash_mismatch');
create temp table d1 as select enigma_complete_job('a2000000-0000-0000-0000-000000000001',(select id from c1),'owner-1',2,
  encode(digest(convert_to('hello','UTF8'),'sha256'),'hex'),'{"ttft_ms":12.5,"output_tokens":2}'::jsonb) as r;
select zz_check('complete ok', (select r->>'ok' from d1) = 'true');
select zz_check('assistant message stored exactly once with measured meta',
  (select count(*) from messages where job_id = (select id from c1) and role = 'assistant' and content = 'hello') = 1
  and (select meta->'measured'->>'ttft_ms' from messages where job_id = (select id from c1) and role='assistant') = '12.5');
select zz_check('complete is idempotent',
  (enigma_complete_job('a2000000-0000-0000-0000-000000000001',(select id from c1),'owner-1',2, encode(digest(convert_to('hello','UTF8'),'sha256'),'hex'),'{}'::jsonb)) ->> 'idempotent' = 'true');
select zz_check('complete with different text conflicts',
  (enigma_complete_job('a2000000-0000-0000-0000-000000000001',(select id from c1),'owner-1',2, repeat('1',64),'{}'::jsonb)) ->> 'error' = 'conflict');
select zz_check('still exactly one assistant message', (select count(*) from messages where job_id = (select id from c1) and role='assistant') = 1);
select zz_check('events after terminal rejected',
  (enigma_append_events('a2000000-0000-0000-0000-000000000001',(select id from c1),'owner-1','[{"event_seq":3,"kind":"delta","text":"x"}]'::jsonb)) ->> 'error' = 'terminal');

-- expired lease -> reclaim with attempt++, reset event; then attempts exhausted -> failed
create temp table j2 as select enigma_submit_job('a0000000-0000-0000-0000-000000000001','a3000000-0000-0000-0000-000000000001','a2000000-0000-0000-0000-000000000001','user_A','idem-job-0002','again','{"max_tokens":16,"temperature":0}',4000) as r;
create temp table c2 as select * from enigma_claim_job('a2000000-0000-0000-0000-000000000001','owner-1',30);
select enigma_append_events('a2000000-0000-0000-0000-000000000001',(select id from c2),'owner-1','[{"event_seq":1,"kind":"delta","text":"partial"}]'::jsonb);
update inference_jobs set lease_expires_at = now() - interval '1 second' where id = (select id from c2);
select zz_check('stale owner cannot append after lease expiry',
  (enigma_append_events('a2000000-0000-0000-0000-000000000001',(select id from c2),'owner-1','[{"event_seq":2,"kind":"delta","text":"x"}]'::jsonb)) ->> 'error' = 'lease_lost');
create temp table c2b as select * from enigma_claim_job('a2000000-0000-0000-0000-000000000001','owner-2',30);
select zz_check('expired lease re-claimed by new owner with attempt 2', (select attempt from c2b) = 2 and (select lease_owner from c2b) = 'owner-2');
select zz_check('old owner is locked out after re-claim',
  (enigma_append_events('a2000000-0000-0000-0000-000000000001',(select id from c2),'owner-1','[{"event_seq":2,"kind":"delta","text":"x"}]'::jsonb)) ->> 'error' = 'lease_lost');
select enigma_append_events('a2000000-0000-0000-0000-000000000001',(select id from c2b),'owner-2','[{"event_seq":2,"kind":"reset"},{"event_seq":3,"kind":"delta","text":"fresh"}]'::jsonb);
select zz_check('job text after reset only has the new attempt', enigma_job_text((select id from c2b)) = 'fresh');
update inference_jobs set lease_expires_at = now() - interval '1 second' where id = (select id from c2b);
select zz_check('no attempts left: not re-claimable', (select count(*) from enigma_claim_job('a2000000-0000-0000-0000-000000000001','owner-3',30)) = 0);
select zz_check('exhausted job is failed lease_expired', (select status from inference_jobs where id = (select id from c2b)) = 'failed'
  and (select error_code from inference_jobs where id = (select id from c2b)) = 'lease_expired');

-- cancellation
create temp table j3 as select enigma_submit_job('a0000000-0000-0000-0000-000000000001','a3000000-0000-0000-0000-000000000001','a2000000-0000-0000-0000-000000000001','user_A','idem-job-0003','cancel me','{"max_tokens":16,"temperature":0}',4000) as r;
select zz_check('cancel of pending job is immediate', (enigma_request_cancel('a0000000-0000-0000-0000-000000000001',(select (r->>'job_id')::uuid from j3))) ->> 'status' = 'cancelled');
select zz_check('cross-tenant cancel is not_found', (enigma_request_cancel('b0000000-0000-0000-0000-000000000001',(select (r->>'job_id')::uuid from j3))) ->> 'error' = 'not_found');
create temp table j4 as select enigma_submit_job('a0000000-0000-0000-0000-000000000001','a3000000-0000-0000-0000-000000000001','a2000000-0000-0000-0000-000000000001','user_A','idem-job-0004','cancel me too','{"max_tokens":16,"temperature":0}',4000) as r;
create temp table c4 as select * from enigma_claim_job('a2000000-0000-0000-0000-000000000001','owner-1',30);
select enigma_append_events('a2000000-0000-0000-0000-000000000001',(select id from c4),'owner-1','[{"event_seq":1,"kind":"delta","text":"abc"}]'::jsonb);
select enigma_request_cancel('a0000000-0000-0000-0000-000000000001',(select id from c4));
select zz_check('events response surfaces cancel flag',
  (enigma_append_events('a2000000-0000-0000-0000-000000000001',(select id from c4),'owner-1','[{"event_seq":2,"kind":"delta","text":"d"}]'::jsonb)) ->> 'cancel_requested' = 'true');
create temp table ack4 as select enigma_ack_cancel('a2000000-0000-0000-0000-000000000001',(select id from c4),'owner-1') as r;
select zz_check('ack-cancel closes job as cancelled', (select r->>'status' from ack4) = 'cancelled');
select zz_check('ack-cancel stores the partial text, flagged cancelled',
  (select content from messages where job_id = (select id from c4) and role='assistant') = 'abcd'
  and (select (meta->>'cancelled')::boolean from messages where job_id = (select id from c4) and role='assistant'));

-- caps
update inference_jobs set status = 'failed', finished_at = now() where status in ('pending','claimed','running','cancel_requested');
do $$ begin for i in 1..4 loop
  perform enigma_submit_job('a0000000-0000-0000-0000-000000000001','a3000000-0000-0000-0000-000000000001','a2000000-0000-0000-0000-000000000001', 'user_cap'||i, 'idem-cap-000'||i,'p','{"max_tokens":16,"temperature":0}',4000);
end loop; end $$;
select zz_check('per-runtime active job cap enforced',
  (enigma_submit_job('a0000000-0000-0000-0000-000000000001','a3000000-0000-0000-0000-000000000001','a2000000-0000-0000-0000-000000000001','user_cap9','idem-cap-0009','p','{"max_tokens":16,"temperature":0}',4000)) ->> 'error' = 'too_many_jobs');

-- ------------------------------------------------------------------ telemetry
create temp table t1 as select enigma_ingest_telemetry('a2000000-0000-0000-0000-000000000001','batch-0001', null,
  '[{"captured_at":"2026-01-01T00:00:00Z","gpu_index":0,"util_gpu_pct":50,"mem_used_mib":100,"mem_total_mib":15360,"temp_c":null}]'::jsonb,
  '[{"captured_at":"2026-01-01T00:00:00Z","num_requests_running":1,"kv_cache_usage_perc":null}]'::jsonb) as r;
select zz_check('telemetry ingested', (select r->>'gpu_rows' from t1) = '1' and (select r->>'vllm_rows' from t1) = '1');
select zz_check('missing sensors stay NULL, never 0',
  (select temp_c is null from gpu_samples where gpu_index = 0 and captured_at = '2026-01-01T00:00:00Z' and runtime_id = 'a2000000-0000-0000-0000-000000000001')
  and (select kv_cache_usage_perc is null from vllm_metric_samples where captured_at = '2026-01-01T00:00:00Z'));
select zz_check('telemetry batch replay is a no-op',
  (enigma_ingest_telemetry('a2000000-0000-0000-0000-000000000001','batch-0001', null, '[]'::jsonb, '[]'::jsonb)) ->> 'duplicate' = 'true');
select zz_check('telemetry rows inherit the runtime tenant',
  (select workspace_id from gpu_samples where captured_at = '2026-01-01T00:00:00Z') = 'a0000000-0000-0000-0000-000000000001');
select zz_check('telemetry for unknown runtime rejected',
  (enigma_ingest_telemetry('c2000000-0000-0000-0000-000000000009','batch-0002', null, '[]'::jsonb, '[]'::jsonb)) ->> 'error' = 'not_found');
select zz_check('mock runtime rows are flagged mock', (select bool_and(is_mock) from gpu_samples where runtime_id = 'a2000000-0000-0000-0000-000000000001' and captured_at = '2026-01-01T00:00:00Z'));
select zz_check('oversized batch rejected',
  (enigma_ingest_telemetry('a2000000-0000-0000-0000-000000000001','batch-0003', null,
    (select jsonb_agg(jsonb_build_object('captured_at', now() + (i||' ms')::interval, 'gpu_index', 0)) from generate_series(1,601) i), '[]'::jsonb)) ->> 'error' = 'batch_too_large');

-- runs + reaping
create temp table run1 as select enigma_start_run('a2000000-0000-0000-0000-000000000001','{"model":"mock-model","tp":1,"pp":1,"max_model_len":2048}'::jsonb) as id;
select zz_check('run started', (select id from run1) is not null);
select enigma_heartbeat('a2000000-0000-0000-0000-000000000001');
select zz_check('fresh heartbeat run not reaped', enigma_reap_runs(90) = 0);
update runtimes set last_heartbeat_at = now() - interval '10 minutes' where id = 'a2000000-0000-0000-0000-000000000001';
create temp table reaped as select enigma_reap_runs(90) as n;
select zz_check('stale runtime run is aborted', (select n from reaped) = 1 and (select status from inference_runs where id = (select id from run1)) = 'aborted');

-- audit trail append-only even for service role
insert into audit_events (workspace_id, actor, action) values ('a0000000-0000-0000-0000-000000000001','user_A','test');
select zz_raises('audit rows cannot be updated', $$update audit_events set action = 'x'$$);
select zz_raises('audit rows cannot be deleted', $$delete from audit_events$$);

-- retention prune
select zz_check('prune runs and is bounded', (enigma_prune(0, 0, 0)) ? 'gpu_samples');
-- onboarding
create temp table ws1 as select enigma_create_workspace('user_new','New WS','new-ws') as r;
select zz_check('create_workspace is atomic: workspace + owner + default project',
  (select r->>'ok' from ws1) = 'true'
  and (select role from memberships where user_id = 'user_new') = 'owner'
  and (select count(*) from projects where workspace_id = ((select r->'workspace'->>'id' from ws1))::uuid) = 1);
select zz_check('duplicate slug rejected', (enigma_create_workspace('user_other','x','new-ws')) ->> 'error' = 'slug_taken');

reset role;

select count(*) || ' SQL checks passed' as summary from zz_results;
