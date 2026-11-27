-- Self-hosted Better Auth (better-auth 1.7.x core + emailOTP + jwt plugins) in its own schema `auth`. Idempotent.
-- Column names and types match the Better Auth tables Neon Managed Better Auth used (neon_auth.*), so existing users, credential (scrypt) hashes and
-- UUIDs import unchanged (see scripts/oci_import_auth.py). Organization/admin-plugin tables are NOT created: Enigma never used them.
-- Access: only the NOLOGIN group role `enigma_auth_app` (granted to the web tier's login role) may read/write this schema. The API role `enigma_app`
-- gets nothing here: the API trusts only signed tokens, never the auth tables.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'enigma_auth_app') then
    create role enigma_auth_app nologin nosuperuser nocreatedb nocreaterole nobypassrls;
  end if;
end $$;

create schema if not exists auth;
revoke all on schema auth from public;

create table if not exists auth."user" (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  email text not null unique,
  "emailVerified" boolean not null,
  image text,
  "createdAt" timestamptz not null default current_timestamp,
  "updatedAt" timestamptz not null default current_timestamp,
  role text,
  banned boolean,
  "banReason" text,
  "banExpires" timestamptz
);

create table if not exists auth.account (
  id uuid primary key default gen_random_uuid(),
  "accountId" text not null,
  "providerId" text not null,
  "userId" uuid not null references auth."user"(id) on delete cascade,
  "accessToken" text,
  "refreshToken" text,
  "idToken" text,
  "accessTokenExpiresAt" timestamptz,
  "refreshTokenExpiresAt" timestamptz,
  scope text,
  password text,
  "createdAt" timestamptz not null default current_timestamp,
  "updatedAt" timestamptz not null
);
create index if not exists "account_userId_idx" on auth.account ("userId");

create table if not exists auth.session (
  id uuid primary key default gen_random_uuid(),
  "expiresAt" timestamptz not null,
  token text not null unique,
  "createdAt" timestamptz not null default current_timestamp,
  "updatedAt" timestamptz not null,
  "ipAddress" text,
  "userAgent" text,
  "userId" uuid not null references auth."user"(id) on delete cascade
);
create index if not exists "session_userId_idx" on auth.session ("userId");
create index if not exists "session_expiresAt_idx" on auth.session ("expiresAt");

create table if not exists auth.verification (
  id uuid primary key default gen_random_uuid(),
  identifier text not null,
  value text not null,
  "expiresAt" timestamptz not null,
  "createdAt" timestamptz not null default current_timestamp,
  "updatedAt" timestamptz not null default current_timestamp
);
create index if not exists verification_identifier_idx on auth.verification (identifier);

create table if not exists auth.jwks (
  id uuid primary key default gen_random_uuid(),
  "publicKey" text not null,
  "privateKey" text not null,
  "createdAt" timestamptz not null,
  "expiresAt" timestamptz,
  alg text,
  crv text
);

grant usage on schema auth to enigma_auth_app;
grant select, insert, update, delete on all tables in schema auth to enigma_auth_app;
revoke all on schema auth from enigma_app;
revoke all on all tables in schema auth from enigma_app;
