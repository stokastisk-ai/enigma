# Security model

| Boundary | Control |
|---|---|
| Browser -> web | Clerk session; `proxy.ts` protects every non-public route and **fails closed** if neither Clerk nor the explicit dev mode is configured |
| Browser -> API | Only via the BFF: allow-listed method+path, zod body validation, query allow-list, 128 KB cap, no prompt echo in errors, bearer never reaches the browser |
| User -> data | FastAPI checks workspace membership + role on every route; non-members get **404** (no existence leak). Direct browser reads (Realtime) are protected by RLS keyed on Clerk `sub` |
| GPU host -> API | `enig_rt_<8>_<256-bit>` token; only SHA-256 stored; constant-time compare; scoped to one runtime/project/workspace; expiry, rotation (old key dead immediately), revocation; mock/real binding |
| Tenant isolation | RLS on every table (select-only for `authenticated`, nothing for `anon`), composite FKs `(id, workspace_id)`, SQL functions re-check ids; tested with two tenants |
| Realtime | private channels only; RLS allows members to *receive* `conv:<id>`; **no** publish policy |
| Abuse | body caps (ASGI, incl. chunked), per-route limiters (best-effort per instance), SQL caps: active jobs per runtime/user, events/chars per job, rows per batch, 24 h timestamp window, deadline per job |
| Secrets | service-role key server-only; `NEXT_PUBLIC_*` limited to Clerk publishable + Supabase anon; token never logged/printed (`Config.__repr__` hides it); startup refuses unsafe production config |
| Privacy | prompts are stored (chat history) but never logged; validation errors omit values; failure messages from vLLM are not forwarded |
| Audit | append-only `audit_events` (UPDATE/DELETE revoked even for service role) for workspace, runtime, credential and cancel actions |

Dev auth (HS256) is accepted only when `ENIGMA_DEV_AUTH` is on, never on Railway or with `ENIGMA_ENV=production`, and the UI shows a banner. Algorithm confusion is blocked (`alg=none`, HS256-with-public-key rejected).

Known gaps: rate limits are per warm instance; credential `last_used_at` is updated per request; RLS/Realtime/Clerk JWKS have not been exercised against live services (see TEST_REPORT).

## OCI target (migration branch)
- Self-hosted Better Auth: scrypt password hashes, httpOnly SameSite=Lax session cookies (`__Secure-` over HTTPS), 6-digit e-mail codes (10 min, 5
  attempts), Ed25519 JWT signing keys encrypted at rest with `BETTER_AUTH_SECRET`; the catch-all auth handler is not mounted. Enigma's own routes keep
  the same-origin check, strict bodies, generic errors and per-address/per-account rate limits. Sign-up is closed by default (private beta).
- Role separation in PostgreSQL: the web tier's auth role can only use schema `auth`; the API role cannot read it; the audit trail is append-only.
- Edge: only Caddy listens publicly; tenant and admin API routes return 404 there; the database has no network egress and requires TLS 1.3 with
  hostname verification even inside the VM.
- Diagnostics (`/api/v1/diag/events`): separate `edx_` keys (hashed, quota, expiry, revocation), field whitelist, credential-shaped strings refused.
