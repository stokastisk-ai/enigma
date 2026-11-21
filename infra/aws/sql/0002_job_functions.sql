-- Atomic job / telemetry logic, PORTABLE PostgreSQL. Derived from supabase/migrations/0002. Executed only by the FastAPI application role (enigma_app).
-- Every runtime-facing function takes the runtime id that FastAPI derived from the verified token,
-- and re-checks it against the row: a runtime can never touch another runtime's job.

-- caps (kept in SQL so they hold even if the API is bypassed with the service key)
create or replace function public.enigma_cap(name text) returns int language sql immutable as $$
  select case name
    when 'job_events' then 5000
    when 'job_text_chars' then 60000
    when 'active_jobs_per_runtime' then 4
    when 'active_jobs_per_user' then 2
    when 'gpu_rows_per_batch' then 600
    when 'vllm_rows_per_batch' then 120
  end
$$;

create or replace function public.enigma_expire_jobs(p_runtime uuid default null) returns void
language plpgsql set search_path = public as $$
begin
  update inference_jobs set status = 'expired', finished_at = now(), error_code = 'deadline',
         error_message = 'job was not completed before its deadline'
   where status = 'pending' and deadline_at < now() and (p_runtime is null or runtime_id = p_runtime);

  -- a cancel was requested and the connector lease is gone: nobody will ack, close it
  update inference_jobs set status = 'cancelled', finished_at = now()
   where status = 'cancel_requested' and lease_expires_at < now() and (p_runtime is null or runtime_id = p_runtime);

  -- lease expired, no attempts left (or deadline passed): fail instead of re-queueing forever
  update inference_jobs set status = 'failed', finished_at = now(), error_code = 'lease_expired',
         error_message = 'runtime stopped responding and no attempts remain'
   where status in ('claimed','running') and lease_expires_at < now()
     and (attempt >= max_attempts or deadline_at < now())
     and (p_runtime is null or runtime_id = p_runtime);
end $$;

create or replace function public.enigma_claim_job(p_runtime uuid, p_owner text, p_lease_seconds int default 30)
returns setof public.inference_jobs language plpgsql set search_path = public as $$
declare v_lease int := least(greatest(p_lease_seconds, 5), 120);
begin
  if not exists (select 1 from runtimes where id = p_runtime and disabled_at is null) then
    return;
  end if;
  perform enigma_expire_jobs(p_runtime);
  return query
  update inference_jobs j
     set status = 'claimed', lease_owner = p_owner, lease_expires_at = now() + make_interval(secs => v_lease),
         attempt = j.attempt + 1, claimed_at = now(), first_token_at = null
   where j.id = (
     select id from inference_jobs
      where runtime_id = p_runtime and deadline_at > now()
        and (status = 'pending' or (status in ('claimed','running') and lease_expires_at < now() and attempt < max_attempts))
      order by created_at
      for update skip locked limit 1)
  returning j.*;
end $$;

create or replace function public.enigma_append_events(p_runtime uuid, p_job uuid, p_owner text, p_events jsonb, p_lease_seconds int default 30)
returns jsonb language plpgsql set search_path = public as $$
declare
  j inference_jobs; e jsonb; v_seq int; v_last int; v_accepted int := 0; v_dups int := 0; v_first_new int;
  v_len int; v_has_delta boolean := false; v_lease int := least(greatest(p_lease_seconds, 5), 120);
begin
  select * into j from inference_jobs where id = p_job and runtime_id = p_runtime for update;
  if not found then return jsonb_build_object('error','not_found'); end if;
  if j.status in ('succeeded','failed','cancelled','expired') then
    return jsonb_build_object('error','terminal','status', j.status);
  end if;
  if j.lease_owner is distinct from p_owner or j.lease_expires_at is null or j.lease_expires_at < now() then
    return jsonb_build_object('error','lease_lost');
  end if;

  v_last := j.last_event_seq;
  for e in select value from jsonb_array_elements(p_events) order by (value->>'event_seq')::int loop
    v_seq := (e->>'event_seq')::int;
    if v_seq <= v_last then v_dups := v_dups + 1; continue; end if;
    if v_seq > v_last + 1 then
      -- gap: persist what we accepted so far, tell the connector what we expect next
      update inference_jobs set last_event_seq = v_last, event_count = j.event_count + v_accepted, text_chars = j.text_chars,
             lease_expires_at = now() + make_interval(secs => v_lease) where id = p_job;
      return jsonb_build_object('error','gap','expected_seq', v_last + 1, 'last_event_seq', v_last, 'accepted', v_accepted);
    end if;
    v_len := coalesce(char_length(e->>'text'), 0);
    if j.event_count + v_accepted + 1 > enigma_cap('job_events') or j.text_chars + v_len > enigma_cap('job_text_chars') then
      update inference_jobs set last_event_seq = v_last, event_count = j.event_count + v_accepted,
             status = 'failed', finished_at = now(), error_code = 'cap_exceeded',
             error_message = 'job output exceeded the event or text cap'  where id = p_job;
      return jsonb_build_object('error','cap_exceeded');
    end if;
    insert into inference_events (job_id, event_seq, workspace_id, kind, text, token_count, emitted_at)
    values (p_job, v_seq, j.workspace_id, coalesce(e->>'kind','delta'), e->>'text',
            nullif(e->>'token_count','')::int, nullif(e->>'emitted_at','')::timestamptz);
    if coalesce(e->>'kind','delta') = 'reset' then j.text_chars := 0; end if;
    if coalesce(e->>'kind','delta') = 'delta' then v_has_delta := true; j.text_chars := j.text_chars + v_len; end if;
    if v_first_new is null then v_first_new := v_seq; end if;
    v_last := v_seq; v_accepted := v_accepted + 1;
  end loop;

  update inference_jobs set last_event_seq = v_last, event_count = event_count + v_accepted, text_chars = j.text_chars,
         status = case when status = 'cancel_requested' then status else 'running' end,
         first_token_at = case when v_has_delta then coalesce(first_token_at, now()) else first_token_at end,
         lease_expires_at = now() + make_interval(secs => v_lease)
   where id = p_job;
  return jsonb_build_object('ok', true, 'last_event_seq', v_last, 'accepted', v_accepted, 'duplicates', v_dups,
                            'first_new_seq', v_first_new, 'cancel_requested', j.status = 'cancel_requested');
end $$;

-- text of the current attempt: deltas after the last reset event
create or replace function public.enigma_job_text(p_job uuid) returns text language sql stable set search_path = public as $$
  select coalesce(string_agg(text, '' order by event_seq), '') from inference_events
   where job_id = p_job and kind = 'delta'
     and event_seq > coalesce((select max(event_seq) from inference_events where job_id = p_job and kind = 'reset'), 0)
$$;

create or replace function public.enigma_complete_job(p_runtime uuid, p_job uuid, p_owner text, p_final_seq int, p_text_sha256 text, p_result jsonb)
returns jsonb language plpgsql set search_path = public as $$
declare j inference_jobs; v_text text; v_msg uuid;
begin
  select * into j from inference_jobs where id = p_job and runtime_id = p_runtime for update;
  if not found then return jsonb_build_object('error','not_found'); end if;
  if j.status = 'succeeded' then
    if j.result ->> 'text_sha256' = p_text_sha256 then
      return jsonb_build_object('ok', true, 'idempotent', true, 'assistant_message_id', j.assistant_message_id);
    end if;
    return jsonb_build_object('error','conflict');
  end if;
  if j.status in ('failed','cancelled','expired') then return jsonb_build_object('error','terminal','status', j.status); end if;
  if j.lease_owner is distinct from p_owner or j.lease_expires_at < now() then return jsonb_build_object('error','lease_lost'); end if;
  if p_final_seq <> j.last_event_seq then
    return jsonb_build_object('error','seq_mismatch','expected_seq', j.last_event_seq);
  end if;
  v_text := enigma_job_text(p_job);
  if encode(sha256(convert_to(v_text, 'UTF8')), 'hex') <> p_text_sha256 then
    return jsonb_build_object('error','hash_mismatch');
  end if;
  insert into messages (workspace_id, conversation_id, role, content, job_id, meta)
  values (j.workspace_id, j.conversation_id, 'assistant', v_text, p_job,
          jsonb_build_object('measured', coalesce(p_result, '{}'::jsonb), 'runtime_id', j.runtime_id))
  returning id into v_msg;
  update inference_jobs set status = 'succeeded', finished_at = now(), assistant_message_id = v_msg,
         result = coalesce(p_result, '{}'::jsonb) || jsonb_build_object('text_sha256', p_text_sha256), lease_expires_at = null
   where id = p_job;
  update conversations set updated_at = now() where id = j.conversation_id;
  return jsonb_build_object('ok', true, 'assistant_message_id', v_msg);
end $$;

create or replace function public.enigma_fail_job(p_runtime uuid, p_job uuid, p_owner text, p_code text, p_message text, p_retryable boolean)
returns jsonb language plpgsql set search_path = public as $$
declare j inference_jobs;
begin
  select * into j from inference_jobs where id = p_job and runtime_id = p_runtime for update;
  if not found then return jsonb_build_object('error','not_found'); end if;
  if j.status in ('succeeded','failed','cancelled','expired') then
    return jsonb_build_object('ok', true, 'idempotent', true, 'status', j.status);
  end if;
  if j.lease_owner is distinct from p_owner or j.lease_expires_at < now() then return jsonb_build_object('error','lease_lost'); end if;
  if p_retryable and j.attempt < j.max_attempts and j.status <> 'cancel_requested' then
    update inference_jobs set status = 'pending', lease_owner = null, lease_expires_at = null,
           error_code = left(p_code, 60), error_message = left(p_message, 500) where id = p_job;
    return jsonb_build_object('ok', true, 'status', 'pending', 'requeued', true);
  end if;
  update inference_jobs set status = case when status = 'cancel_requested' then 'cancelled' else 'failed' end,
         finished_at = now(), error_code = left(p_code, 60), error_message = left(p_message, 500), lease_expires_at = null
   where id = p_job;
  return jsonb_build_object('ok', true, 'status', 'failed');
end $$;

create or replace function public.enigma_ack_cancel(p_runtime uuid, p_job uuid, p_owner text)
returns jsonb language plpgsql set search_path = public as $$
declare j inference_jobs; v_text text;
begin
  select * into j from inference_jobs where id = p_job and runtime_id = p_runtime for update;
  if not found then return jsonb_build_object('error','not_found'); end if;
  if j.status in ('succeeded','failed','cancelled','expired') then
    return jsonb_build_object('ok', true, 'idempotent', true, 'status', j.status);
  end if;
  if j.lease_owner is distinct from p_owner or j.lease_expires_at < now() then return jsonb_build_object('error','lease_lost'); end if;
  if j.status <> 'cancel_requested' then return jsonb_build_object('error','not_cancel_requested','status', j.status); end if;
  v_text := enigma_job_text(p_job);
  if v_text <> '' then
    insert into messages (workspace_id, conversation_id, role, content, job_id, meta)
    values (j.workspace_id, j.conversation_id, 'assistant', v_text, p_job, jsonb_build_object('cancelled', true));
  end if;
  update inference_jobs set status = 'cancelled', finished_at = now(), lease_expires_at = null where id = p_job;
  return jsonb_build_object('ok', true, 'status', 'cancelled');
end $$;

-- user-side cancel (authorisation = workspace match; membership is checked by FastAPI before calling)
create or replace function public.enigma_request_cancel(p_workspace uuid, p_job uuid)
returns jsonb language plpgsql set search_path = public as $$
declare j inference_jobs;
begin
  select * into j from inference_jobs where id = p_job and workspace_id = p_workspace for update;
  if not found then return jsonb_build_object('error','not_found'); end if;
  if j.status = 'pending' then
    update inference_jobs set status = 'cancelled', finished_at = now(), cancel_requested_at = now() where id = p_job;
    return jsonb_build_object('ok', true, 'status', 'cancelled');
  elsif j.status in ('claimed','running') then
    update inference_jobs set status = 'cancel_requested', cancel_requested_at = now() where id = p_job;
    return jsonb_build_object('ok', true, 'status', 'cancel_requested');
  end if;
  return jsonb_build_object('ok', true, 'status', j.status, 'idempotent', true);
end $$;

create or replace function public.enigma_submit_job(p_workspace uuid, p_conversation uuid, p_runtime uuid, p_user text,
    p_idem text, p_content text, p_params jsonb, p_char_budget int)
returns jsonb language plpgsql set search_path = public as $$
declare
  v_conv conversations; v_rt runtimes; v_existing inference_jobs; v_msg uuid; v_hist jsonb; v_job inference_jobs;
begin
  select * into v_conv from conversations where id = p_conversation and workspace_id = p_workspace for update;
  if not found then return jsonb_build_object('error','not_found'); end if;
  select * into v_rt from runtimes where id = p_runtime and workspace_id = p_workspace and project_id = v_conv.project_id and disabled_at is null;
  if not found then return jsonb_build_object('error','runtime_not_found'); end if;

  select * into v_existing from inference_jobs where workspace_id = p_workspace and idempotency_key = p_idem;
  if found then
    if v_existing.conversation_id <> p_conversation then return jsonb_build_object('error','idempotency_conflict'); end if;
    return jsonb_build_object('ok', true, 'job_id', v_existing.id, 'status', v_existing.status, 'idempotent', true);
  end if;

  if char_length(p_content) > p_char_budget then return jsonb_build_object('error','prompt_too_long','char_budget', p_char_budget); end if;
  if (select count(*) from inference_jobs where runtime_id = p_runtime and status in ('pending','claimed','running','cancel_requested'))
       >= enigma_cap('active_jobs_per_runtime') then return jsonb_build_object('error','too_many_jobs'); end if;
  if (select count(*) from inference_jobs where requested_by = p_user and workspace_id = p_workspace
        and status in ('pending','claimed','running','cancel_requested')) >= enigma_cap('active_jobs_per_user')
     then return jsonb_build_object('error','too_many_user_jobs'); end if;

  insert into messages (workspace_id, conversation_id, role, content) values (p_workspace, p_conversation, 'user', p_content)
  returning id into v_msg;

  -- newest-first history within the character budget (current prompt always included), then restore order
  select coalesce(jsonb_agg(jsonb_build_object('role', role, 'content', content) order by ordinal), '[]'::jsonb) into v_hist from (
    select role, content, ordinal, sum(char_length(content)) over (order by ordinal desc) as running
      from messages where conversation_id = p_conversation and role in ('user','assistant')
  ) h where running <= p_char_budget;

  insert into inference_jobs (workspace_id, project_id, conversation_id, runtime_id, requested_by, idempotency_key, request)
  values (p_workspace, v_conv.project_id, p_conversation, p_runtime, p_user, p_idem,
          jsonb_build_object('messages', v_hist, 'max_tokens', (p_params->>'max_tokens')::int,
                             'temperature', (p_params->>'temperature')::numeric, 'model', v_rt.served_model))
  returning * into v_job;
  update conversations set updated_at = now(), runtime_id = p_runtime where id = p_conversation;
  return jsonb_build_object('ok', true, 'job_id', v_job.id, 'status', v_job.status, 'user_message_id', v_msg);
end $$;

create or replace function public.enigma_heartbeat(p_runtime uuid) returns timestamptz
language sql set search_path = public as $$
  update runtimes set last_heartbeat_at = now() where id = p_runtime and disabled_at is null returning last_heartbeat_at
$$;

-- runs whose runtime stopped heartbeating are aborted (called lazily by list endpoints)
create or replace function public.enigma_reap_runs(p_stale_seconds int default 90) returns int
language plpgsql set search_path = public as $$
declare n int;
begin
  update inference_runs r set status = 'aborted', ended_at = coalesce(rt.last_heartbeat_at, now())
    from runtimes rt
   where r.runtime_id = rt.id and r.status = 'active'
     and (rt.last_heartbeat_at is null or rt.last_heartbeat_at < now() - make_interval(secs => p_stale_seconds));
  get diagnostics n = row_count;
  return n;
end $$;

create or replace function public.enigma_start_run(p_runtime uuid, p_meta jsonb) returns uuid
language plpgsql set search_path = public as $$
declare rt runtimes; v_id uuid;
begin
  select * into rt from runtimes where id = p_runtime and disabled_at is null;
  if not found then return null; end if;
  update inference_runs set status = 'aborted', ended_at = now() where runtime_id = p_runtime and status = 'active';
  insert into inference_runs (workspace_id, project_id, runtime_id, is_mock, model, tp, pp, max_model_len)
  values (rt.workspace_id, rt.project_id, p_runtime, rt.is_mock, p_meta->>'model',
          nullif(p_meta->>'tp','')::int, nullif(p_meta->>'pp','')::int, nullif(p_meta->>'max_model_len','')::int)
  returning id into v_id;
  return v_id;
end $$;

create or replace function public.enigma_ingest_telemetry(p_runtime uuid, p_batch_id text, p_run uuid, p_gpu jsonb, p_vllm jsonb)
returns jsonb language plpgsql set search_path = public as $$
declare rt runtimes; n_gpu int := 0; n_vllm int := 0;
begin
  select * into rt from runtimes where id = p_runtime and disabled_at is null;
  if not found then return jsonb_build_object('error','not_found'); end if;
  if jsonb_array_length(coalesce(p_gpu,'[]')) > enigma_cap('gpu_rows_per_batch')
     or jsonb_array_length(coalesce(p_vllm,'[]')) > enigma_cap('vllm_rows_per_batch') then
    return jsonb_build_object('error','batch_too_large');
  end if;
  if p_run is not null and not exists (select 1 from inference_runs where id = p_run and runtime_id = p_runtime) then
    return jsonb_build_object('error','run_not_found');
  end if;
  insert into ingest_batches (runtime_id, batch_id) values (p_runtime, p_batch_id) on conflict do nothing;
  if not found then return jsonb_build_object('ok', true, 'duplicate', true); end if;

  insert into gpu_samples (workspace_id, project_id, runtime_id, run_id, captured_at, gpu_index, gpu_uuid, util_gpu_pct, util_mem_pct,
      mem_used_mib, mem_total_mib, temp_c, power_w, power_limit_w, sm_clock_mhz, mem_clock_mhz, pcie_rx_kib_s, pcie_tx_kib_s, is_mock)
  select rt.workspace_id, rt.project_id, p_runtime, p_run, (g->>'captured_at')::timestamptz, (g->>'gpu_index')::smallint, g->>'gpu_uuid',
         (g->>'util_gpu_pct')::real, (g->>'util_mem_pct')::real, (g->>'mem_used_mib')::real, (g->>'mem_total_mib')::real,
         (g->>'temp_c')::real, (g->>'power_w')::real, (g->>'power_limit_w')::real, (g->>'sm_clock_mhz')::real,
         (g->>'mem_clock_mhz')::real, (g->>'pcie_rx_kib_s')::real, (g->>'pcie_tx_kib_s')::real, rt.is_mock
    from jsonb_array_elements(coalesce(p_gpu,'[]')) g
  on conflict (runtime_id, gpu_index, captured_at) do nothing;
  get diagnostics n_gpu = row_count;

  insert into vllm_metric_samples (workspace_id, project_id, runtime_id, run_id, captured_at, counter_epoch, num_requests_running,
      num_requests_waiting, kv_cache_usage_perc, prompt_tokens_total, generation_tokens_total, preemptions_total,
      request_success, ttft_hist, e2e_hist, itl_hist, is_mock)
  select rt.workspace_id, rt.project_id, p_runtime, p_run, (v->>'captured_at')::timestamptz, coalesce((v->>'counter_epoch')::int, 0),
         (v->>'num_requests_running')::real, (v->>'num_requests_waiting')::real, (v->>'kv_cache_usage_perc')::real,
         (v->>'prompt_tokens_total')::double precision, (v->>'generation_tokens_total')::double precision,
         (v->>'preemptions_total')::double precision, v->'request_success', v->'ttft_hist', v->'e2e_hist', v->'itl_hist', rt.is_mock
    from jsonb_array_elements(coalesce(p_vllm,'[]')) v
  on conflict (runtime_id, captured_at) do nothing;
  get diagnostics n_vllm = row_count;
  return jsonb_build_object('ok', true, 'gpu_rows', n_gpu, 'vllm_rows', n_vllm);
end $$;

-- bounded retention; run from a scheduled one-shot (cron endpoint), never a resident process
create or replace function public.enigma_prune(p_event_hours int default 48, p_gpu_days int default 14, p_vllm_days int default 14)
returns jsonb language plpgsql set search_path = public as $$
declare a int; b int; c int; d int;
begin
  delete from inference_events e using inference_jobs j
   where e.job_id = j.id and j.status in ('succeeded','failed','cancelled','expired')
     and e.received_at < now() - make_interval(hours => p_event_hours);
  get diagnostics a = row_count;
  delete from gpu_samples where captured_at < now() - make_interval(days => p_gpu_days);
  get diagnostics b = row_count;
  delete from vllm_metric_samples where captured_at < now() - make_interval(days => p_vllm_days);
  get diagnostics c = row_count;
  delete from ingest_batches where received_at < now() - interval '2 days';
  get diagnostics d = row_count;
  return jsonb_build_object('events', a, 'gpu_samples', b, 'vllm_samples', c, 'batches', d);
end $$;

-- atomic onboarding: workspace + owner membership + default project in one transaction
create or replace function public.enigma_create_workspace(p_user text, p_name text, p_slug text) returns jsonb
language plpgsql set search_path = public as $$
declare ws workspaces; pr projects;
begin
  if exists (select 1 from workspaces where slug = p_slug) then return jsonb_build_object('error','slug_taken'); end if;
  if (select count(*) from memberships where user_id = p_user and role = 'owner') >= 5 then
    return jsonb_build_object('error','workspace_limit');
  end if;
  insert into workspaces (name, slug, created_by) values (p_name, p_slug, p_user) returning * into ws;
  insert into memberships (workspace_id, user_id, role) values (ws.id, p_user, 'owner');
  insert into projects (workspace_id, name, created_by) values (ws.id, 'Default', p_user) returning * into pr;
  insert into audit_events (workspace_id, actor, action, target_type, target_id) values (ws.id, p_user, 'workspace.create', 'workspace', ws.id::text);
  return jsonb_build_object('ok', true, 'workspace', to_jsonb(ws), 'project', to_jsonb(pr));
end $$;

-- only the application role may call these
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname like 'enigma\_%'
  loop
    execute format('revoke all on function %s from public', f.sig);
    execute format('grant execute on function %s to enigma_app', f.sig);
  end loop;
end $$;
