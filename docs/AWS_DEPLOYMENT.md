# AWS deployment plan — NOT DEPLOYED. Awaiting your itemized approval.

**Nothing in this file has been executed.** No AWS resource exists, no stack was created, no subscription enabled. Everything below is a plan for you to approve, amend or reject. Prices, assumptions and sources: `AWS_COST_MATRIX.md` (region `us-east-1`, recommended, not yet approved).

## 1. Recommended architecture
```
Browser ─HTTPS─► CloudFront (default *.cloudfront.net domain; caches /_next/static)
                     └─► Lambda Function URL ─► Lambda: Next.js 16 standalone via Lambda Web Adapter (Node, ARM64, 1 GB)
                                                    │  Cognito cookies (httpOnly), BFF /api/enigma/*, /api/auth/*
                                                    ▼ Bearer <Cognito access token>
Kaggle connector ─outbound HTTPS─► Lambda Function URL ─► Lambda: FastAPI + Mangum (Python 3.13, x86, 512 MB, reserved concurrency 5)
                                                    │ verifies Cognito JWT (pinned JWKS, no network), membership, tenant ids
                                                    ▼
                          Track A: RDS PostgreSQL (current supported major) db.t4g.micro, PRIVATE, same VPC      (implemented, tested)
                      or  Track B: DynamoDB (on-demand/provisioned), no VPC                (designed, NOT built)
Cognito user pool (Lite tier, public client, email sign-up) · CloudWatch Logs (7-day retention) · S3 (code artifacts only)
```
No NAT gateway, no public IPv4, no interface endpoints, no Secrets Manager, no ALB/EC2/ECS/EKS, no Route 53/domain/WAF, no GPU on AWS. The GPU stays on user-launched Kaggle.

**Recommendation.** Web tier: CloudFront + Lambda Web Adapter. API: Lambda Function URL. Identity: Cognito **Lite**. Database: **Track B (DynamoDB) is the only option that can plausibly stay near $0**, but it must first pass the parity gate in `AWS_COST_MATRIX.md` §6. **Track A (RDS) is the only option deployable today** and costs ≈ USD 14/month fixed, funded by credits and then by your card until torn down. If you want a working hosted system soonest, choose Track A with a hard teardown date; if "no out-of-pocket" is the priority, approve the Track B build first.

## 2. Expected monthly charges (USD, us-east-1; list prices; from `scripts/aws_cost_model.py`)
| Workload | Track A — LIST | Track A — with free tiers | Track B — LIST | Track B — with free tiers |
|---|---:|---:|---:|---:|
| Low (10 GPU-h, 1 dev) | 14.21 | 13.98 | 0.37 | 0.00 |
| Moderate (60 GPU-h, small team) | 15.51 | 13.99 | 2.40 | 0.01 |
| High (130 GPU-h, dev ceiling) | 19.48 | 14.07 | 7.44 | 0.09 |
"With free tiers" is valid only if the always-free allowances apply to this PAID-plan account (unverified). **Track A's ≈ USD 14 is fixed whatever the usage.** Costs that would appear if the design drifts: NAT gateway +$32.85/mo, a public IPv4 +$3.65/mo, an interface endpoint +$7.30/mo per AZ, Secrets Manager +$0.40/secret/mo, Cognito Essentials instead of Lite 2.7× per MAU above the free 10,000.

## 3. Itemized resource list for approval (nothing created)
| # | Resource | Config | Monthly (list) | Created by |
|---|---|---|---:|---|
| 1 | Cognito user pool + **public** app client | Lite tier, email sign-up, `ALLOW_USER_PASSWORD_AUTH`+`ALLOW_REFRESH_TOKEN_AUTH`, no client secret, default email sender | 0 (≤10k MAU free) | CLI/IaC after approval |
| 2 | Lambda `enigma-api` | python3.13, x86, 512 MB, 15 s, **reserved concurrency 5**, VPC-attached only for Track A | ≈0.0–0.6 | `infra/aws/template.yaml` (draft) |
| 3 | Lambda `enigma-web` | Node, ARM64, 1 GB, Web Adapter layer, standalone bundle + static assets, timeout 20 s, reserved concurrency 5 | ≈0.0–0.5 | not yet drafted |
| 4 | 2 × Function URL | AuthType NONE (public at the edge; app auth + limits are the control) | 0 (no listed charge) | template |
| 5 | CloudFront distribution | default domain, origin = web Function URL (no viewer `Host` forwarding), caching for `/_next/static/*` | 0–3.8 | not yet drafted |
| 6 | CloudWatch log groups (2) | retention 7 days; no custom metrics/alarms | 0.0–0.5 | template |
| 7 | S3 bucket | private, block-public-access, SSE, code zips only | ≈0.00 | needed to hold the Lambda zips |
| 8a | **Track A:** RDS PostgreSQL db.t4g.micro | Single-AZ, 20 GB gp3, **private**, encrypted, 1-day backups, deletion protection OFF for dev, subnet group in 2 AZs (free), security group allowing only the API Lambda | **13.98** | not yet drafted |
| 8b | **Track B:** DynamoDB tables | on-demand or 25/25 provisioned, TTL, no PITR | 0–1.9 | not built |
| 9 | IAM roles (2) | least privilege: logs write only; API additionally VPC ENI for Track A | 0 | template |
| 10 | AWS Budget + email alert (optional) | notification only — **not a spending cap** | 0 (first budgets free; verify) | needs your OK |

## 4. Hard cost controls to build in before any approval is exercised
- Lambda **reserved concurrency** 5 (API) and 5 (web) bounds parallel invocations and DB connections.
- Connector claim-poll backoff (5 s idle) to cut API volume about 38 % at the ceiling workload; not yet implemented.
- Log retention 7 days; no custom metrics; no alarms except one budget notification.
- A one-command **teardown script** (delete in reverse order; verify empty with the same read-only list calls) must exist and be tested against the template *before* anything is created; deletion order and the RDS final-snapshot choice are in §7.
- Calendar the credit-expiry date; tear down Track A before it if you do not want the card billed.

## 5. Packaging (local, free) and environment names
- **API zip:** `/usr/local/bin/python3.13 -m pip install -r apps/api/requirements-aws.txt -t build/api --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.13 --implementation cp`, then add `apps/api/enigma_api` + `apps/api/lambda_handler.py`. No CUDA/torch/vLLM/nvidia-ml-py.
- **Web bundle:** `cd apps/web && ENIGMA_STANDALONE=1 ENIGMA_DIST_DIR=.next-standalone npm run build`, then copy `.next-standalone/static` and `public/` into the standalone folder, add the Lambda Web Adapter layer, set `AWS_LWA_PORT=3000`, `HOSTNAME=0.0.0.0`, readiness path `/`.
- **Names only** (values never in Git): web `ENIGMA_AUTH_PROVIDER=cognito`, `NEXT_PUBLIC_COGNITO_REGION`, `NEXT_PUBLIC_COGNITO_USER_POOL_ID`, `NEXT_PUBLIC_COGNITO_CLIENT_ID`, `ENIGMA_API_URL`, **`ENIGMA_PUBLIC_HOST`** (CloudFront hostname; required for the CSRF check), **`ENIGMA_PUBLIC_ORIGIN`** (for the sign-in redirect). API: `ENIGMA_ENV=production`, `ENIGMA_STORE=postgres|dynamodb`, `ENIGMA_AUTH_PROVIDER=cognito`, `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, **`COGNITO_JWKS_JSON`** (pinned keys: refresh when Cognito rotates), `ENIGMA_DB_DSN` (Track A; encrypted Lambda environment variable, `sslmode=require` or stricter).

## 6. Deployment sequence (after approval; stop on any unexpected charge or error)
Tags on everything: `Project=Enigma`, `Environment=dev`, `Owner=<owner>`. Use a **non-root** identity and always pass `--profile`/`--region`.
1. Create the non-root admin identity; delete the root access keys (your action or your explicit OK).
2. Cognito pool + client; fetch the pool's JWKS once and store it as `COGNITO_JWKS_JSON`; real two-user test of sign-up/confirm/sign-in/sign-out.
3. Database: Track A (VPC, subnet group, SG, RDS) **or** Track B (after parity gate). Apply `infra/aws/sql/*.sql` to a dedicated database only.
4. API Lambda + Function URL; test `/api/v1/health`, 401 without token, two-tenant isolation with real Cognito tokens, job claim/lease/replay.
5. Web Lambda + Function URL, then CloudFront; verify cookies, sign-in redirect stays on the CloudFront domain, CSRF check passes from the real origin, polling, history after refresh.
6. You launch the Kaggle notebook (2×T4): browser → AWS → Kaggle connector → streamed chunks, per-GPU NVML, vLLM `/metrics`, disconnect/reconnect and stored history.
7. Record every resource ID privately (not in Git) and re-run the cost model against real usage.

## 7. Teardown (reverse order; each step verified with read-only list calls)
CloudFront (disable, wait, delete) → web Lambda + URL → API Lambda + URL → RDS (choose final snapshot or none; snapshots bill $0.095/GB-mo) / DynamoDB tables → security groups, subnets, subnet group → Cognito pool → log groups → S3 objects then bucket → IAM roles → budget. Afterwards confirm in Billing that no usage line remains.

## 8. What I need from you to proceed
1. **Approve or change the region** (`us-east-1`).
2. **Choose Track A, Track B, or both in sequence**, and a monthly spending ceiling (Track A needs ≥ USD 20/month headroom at the ceiling workload).
3. **Credit expiry date** from Billing → Credits, and confirmation that card billing after it is unacceptable (then Track A must be torn down before it).
4. **Non-root deployment identity** and root-key deletion.
5. Approval to install the Track B test tooling (DynamoDB Local or `moto`, `boto3`) if Track B is chosen.
6. A decision on the CloudFront question: keep it (≤$3.8/mo list at the ceiling, fewer Lambda invocations) or skip it (every static asset becomes a Lambda invocation: 16 instead of ~2.5 invocations per page view, about 1.9× more web-tier invocations at the ceiling workload because browser polling dominates).
Until you answer, nothing is created or modified in AWS.

## 9. Local run (unchanged, zero spend)
`/usr/local/bin/python3.13 -m uvicorn index:app --app-dir apps/api --port 8787` · `cd apps/web && npm run build && npx next start -p 3100`. Tests: `/usr/local/bin/python3.13 -m pytest`. No local GPU is ever used (`ENIGMA_FORBID_LOCAL_GPU=1`).
