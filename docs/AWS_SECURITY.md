# AWS security notes

## Implemented and tested locally
- **JWT verification (API)**: RS256 only; HS256 only in explicit dev mode (refused in production/AWS); `alg:none` refused; issuer pinned to the user pool; `token_use` and app-client (`client_id`/`aud`) must match; `exp`/`iat`/`sub` required. 16 tests, locally generated keys (`tests/api/test_cognito_auth.py`).
- **JWT verification (web)** with `node:crypto`; JWKS cached 1 h with one forced refresh on unknown `kid`. 15 tests (`npm run test:unit`).
- **Tenant isolation**: two Cognito identities cannot read each other's workspaces/projects (404, not 403); store-agnostic API suite (27 tests) re-run against real SQL.
- **Cookies**: tokens only in httpOnly, SameSite=Lax cookies (Secure in production); refresh cookie scoped to `/api`. The browser never reads a token.
- **CSRF**: auth POSTs and Cognito-mode BFF POSTs require a same-origin `Origin` header.
- **No account enumeration**: unknown user and wrong password return the identical 401; sign-up conflicts are generic.
- **DB**: least-privilege `enigma_app` (not superuser, no DDL, audit table append-only, no PUBLIC execute); parameterised SQL only; errors expose SQLSTATE only; 8s statement timeout; production refuses `sslmode=disable|allow|prefer`.
- **Dev auth** is refused when `AWS_EXECUTION_ENV`/`AWS_LAMBDA_FUNCTION_NAME`/Vercel markers are present.
- `ENIGMA_COGNITO_TEST_ENDPOINT` is honoured only for loopback and never when `NODE_ENV=production`.

## Not verified / risks
- Nothing was tested against a real Cognito pool, RDS, Lambda or Amplify.
- **Function URL with `AuthType NONE` is public at the edge**; CORS is not authentication. App auth, body caps (128 KiB–1 MiB), rate limiter (in-memory per Lambda environment, so per-instance only — not a global limit) are the controls. A WAF costs extra and is not planned.
- No per-user sign-in throttling beyond Cognito's own limits; no MFA (a challenge response is refused cleanly).
- The deploying identity is currently **root**: replace it with a least-privilege role before any provisioning.
- Previously pasted Clerk/Supabase/Google credentials from earlier chats must be rotated; none were read or copied.
- Existing local `apps/web/.env.local` (Clerk dev keys) is untouched and ignored by git.


## Added 2026-11-21 (cost/deployment review)
- **Root credentials:** the CLI profile authenticates as the account **root user**, and root **access keys exist** (MFA is enabled). Root keys should be deleted and a least-privilege identity used for all deployment work. Nothing was changed (no IAM modifications were authorised).
- **Pinned JWKS (`COGNITO_JWKS_JSON`)** lets a VPC-attached Lambda verify Cognito tokens with no outbound network. Fail-closed on unknown `kid`; malformed JSON refuses to start. Risk: key rotation requires a config update. Tests: `tests/api/test_cognito_auth.py`.
- **CloudFront → Function URL:** the origin sees the Function URL's `Host`, so the CSRF same-origin check accepts only the configured `ENIGMA_PUBLIC_HOST` in addition to `Host`/`X-Forwarded-Host`; the sign-in redirect uses `ENIGMA_PUBLIC_ORIGIN`. CloudFront must not forward the viewer `Host`.
- Function URLs with `AuthType NONE` stay public at the edge; per-instance in-memory rate limiting is not a global limit; reserved concurrency (5) bounds abuse cost.
