-- Enigma hardening for Neon branches that once had the Data API enabled. Idempotent; a no-op where these roles do not exist.
-- Enigma is Browser -> Next.js BFF -> FastAPI -> PostgreSQL. Browsers must never reach tables or functions through a PostgREST role, so the
-- Data API roles (anonymous / authenticated) get nothing in `public`, now or by default privilege for objects created later by the owner.
-- Neon-managed objects (neon_auth, auth schemas, role memberships) are deliberately left untouched.
do $$
declare r text;
begin
  foreach r in array array['anonymous', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('alter default privileges in schema public revoke all on tables from %I', r);
      execute format('alter default privileges in schema public revoke all on sequences from %I', r);
      execute format('alter default privileges in schema public revoke all on functions from %I', r);
      execute format('revoke all on schema public from %I', r);
      execute format('revoke all on all tables in schema public from %I', r);
      execute format('revoke all on all sequences in schema public from %I', r);
      execute format('revoke all on all functions in schema public from %I', r);
    end if;
  end loop;
end $$;
