-- Re-applies 0001's least-privilege grants. Idempotent. Needed when the schema arrives by pg_restore --no-owner --no-privileges (the OCI import from
-- Neon) instead of by 0001, and harmless on a database created by 0001. Tables created later are covered by re-running bootstrap.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'enigma_app') then
    create role enigma_app nologin nosuperuser nocreatedb nocreaterole nobypassrls;
  end if;
end $$;
revoke all on schema public from public;
grant usage on schema public to enigma_app;
revoke all on all tables in schema public from public;
grant select, insert, update, delete on all tables in schema public to enigma_app;
revoke update, delete, truncate on public.audit_events from public, enigma_app;
grant usage, select on all sequences in schema public to enigma_app;
-- the import bookkeeping table is operator-only
do $$
begin
  if to_regclass('public.enigma_import_log') is not null then
    revoke all on public.enigma_import_log from enigma_app;
  end if;
end $$;
