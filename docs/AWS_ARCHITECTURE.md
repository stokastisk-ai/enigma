# Enigma on AWS — architecture (code complete locally, NOT deployed)

AWS hosts only the **control plane**. GPU inference stays on the user-started Kaggle 2×T4 session, which only makes outbound HTTPS calls. No AWS GPU, EC2, ECS/EKS, ALB, NAT or public IP is part of this design.

```
Browser ──► Next.js 16 (Amplify Hosting SSR, apps/web)  ── BFF /api/enigma/* (allow-list, same-origin check)
              │  httpOnly cookies hold Cognito tokens; /api/auth/* talks to the Cognito IDP API
              ▼ Bearer <Cognito access token>
          Lambda (Python 3.13, Mangum)  FastAPI apps/api  ── verifies RS256/iss/client_id/token_use, membership, tenant ids
              ▼ psycopg, role enigma_app (least privilege, no RLS needed)
          PostgreSQL (Track A: RDS)  infra/aws/sql — atomic claim (SKIP LOCKED), event_seq replay, caps, retention
Kaggle connector ──outbound HTTPS, hashed scoped runtime token──► same Lambda API (claim / events / telemetry)
Browser live updates: bounded polling of `GET /jobs/{id}?after_seq=` (Realtime was Supabase-only and is now optional/off)
```

## What changed vs the Vercel/Supabase/Clerk design
| Concern | Before | Now (flag-selected, old path retained) |
|---|---|---|
| Identity | Clerk JWT (`sub`) | `ENIGMA_AUTH_PROVIDER=cognito`: access token, pinned issuer/app-client, `token_use=access`; user id stored as `cognito:<sub>` (never assumed equal to a Clerk id) |
| DB access | PostgREST + service-role key | `ENIGMA_STORE=postgres`: psycopg + `enigma_*` SQL functions, same semantics |
| Isolation | RLS + app checks | App/SQL-function checks (browsers never touch the DB, so there is no policy surface); RLS/`auth.jwt()`/Realtime dropped from the portable schema |
| Hosting | Vercel | Amplify (web) + Lambda (API), draft templates in `infra/aws/` |

## Open decisions (block deployment; see AWS_COST_MATRIX.md)
1. **Lambda↔RDS network path without NAT** — *resolved in code, pending approval:* the API can verify tokens from a pinned JWKS (`COGNITO_JWKS_JSON`), so a VPC Lambda needs no NAT/endpoints; the cost is key-rotation operations. See AWS_COST_MATRIX.md.
2. **DB credential delivery** (`ENIGMA_DB_DSN`): proposed as an encrypted Lambda environment variable (avoids Secrets Manager $0.40/mo and VPC endpoints $7.30/mo/AZ).
3. **Web hosting:** recommended CloudFront + Lambda Web Adapter (Event, ARM64); Lambda Web Functions (Node-only, 2026-10-12) is the watch option. **Amplify SSR is BLOCKED** (AWS documents 12–15). The standalone Node bundle (`ENIGMA_STANDALONE=1`) is validated locally as the basis for a Lambda Web Adapter proposal; see AWS_COST_MATRIX.md.
4. Track A (credit-backed RDS) vs Track B (DynamoDB redesign, not started).
5. Scheduled retention: Lambda handler supports `{"enigma_task":"prune"}` for EventBridge Scheduler; the schedule is not created.

## Track B (DynamoDB) feasibility notes — design only
Single table keyed `WS#<id>`; membership item per user; claim via conditional `UpdateItem` on `(status, lease_expires_at)`; events as `JOB#<id>/EVT#<seq>` with `attribute_not_exists` for idempotent ordered writes; TTL for retention; GSI for runtime→pending jobs. No joins/RLS: every query must carry the verified workspace id. Parity tests would be a rewrite of the SQL suite. Not implemented, not tested.
