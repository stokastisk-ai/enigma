# AWS cost matrix (2026-11-21) — no resource has been created

**Verdict: DEPLOYMENT BLOCKED by the cost gate.** Read-only checks show the account is on the **PAID plan** (status ACTIVE) with **USD 99.99 remaining credits**. A budget alert is not a spending cap, and the full all-AWS app cannot be guaranteed free forever (RDS, public IPv4, Route 53, Secrets Manager, Amplify SSR beyond its allowance all bill). Nothing below is provisioned.

## Account facts (live, read-only)

| Item | Result | Source |
|---|---|---|
| Plan type / status | **PAID / ACTIVE** | `aws freetier get-account-plan-state` |
| Remaining credits | **USD 99.99** | same |
| Credit **expiry date** | **UNKNOWN → BLOCKED** (the CLI does not return it; read it in Billing console → Credits. AWS pages disagree: 6 vs 12 months from sign-up) | https://aws.amazon.com/blogs/aws/aws-free-tier-update-new-customers-can-get-started-and-explore-aws-with-up-to-200-in-credits/ , https://aws.amazon.com/free/free-tier-faqs/ |
| Free Tier usage records | none (`freeTierUsages: []`) | `aws freetier get-free-tier-usage` |
| Region | `us-east-1` (CLI default; **not yet approved by the owner**) | `aws configure get region` |
| Existing Enigma resources | none: EC2, RDS, Amplify apps, Cognito pools, Lambda, S3 buckets, Route 53 zones all returned 0 in us-east-1 | read-only list calls |
| Budgets | none readable (call returned null) | `aws budgets describe-budgets` |
| Caller identity | **root user** (full ARN not recorded here). Using root for deployment is unsafe: create/use a least-privilege role first — needs owner approval (no new IAM users/keys were created) | `aws sts get-caller-identity` |

Credit-backed ≠ free: credits are finite, service-scoped and expire; when they run out, a PAID-plan account is billed to its card.

## Per-service matrix

Outcome: `ELIGIBLE` only when exact current entitlement is proven for this account; otherwise `UNKNOWN`/`BLOCKED`. Prices are public list prices found 2026-11-21 and must be re-checked in the Billing console / Pricing Calculator before any approval.

| Service | Planned use / size | Free allowance (published) | Recurring/dependency charges | Applies to this account? | Outcome |
|---|---|---|---|---|---|
| **Amplify Hosting (Next.js SSR)** | `apps/web`, build per push | 1,000 build min/mo; 500k SSR req + 100 GB-h/mo (tied to Free Tier), then $0.01/build-min, $0.30/M SSR req; WAF $15/mo if enabled | CloudFront delivery inside Amplify; **Next.js 16 support UNVERIFIED** (AWS guide lists 12–15) | Free Tier clock/plan status unknown (PAID plan) | **BLOCKED** — AWS docs list Next.js 12–15 only; a public report says 16.1+ fails on Amplify Hosting (anecdotal). Enigma is on 16.4.6 |
| **Lambda (Python 3.13, FastAPI + Mangum)** | API, short requests | always-free 1M req + 400k GB-s/mo (verify live) | CloudWatch Logs ingestion, VPC networking if attached to RDS | likely, not proven | **UNKNOWN** |
| **Lambda Function URL** | public HTTPS | no separate charge (verify) | none; **public at the edge** even with CORS | — | UNKNOWN (security: app-level auth already mandatory) |
| **API Gateway HTTP API** | alternative front door | 1M calls/mo for 12 months only | $1.00/M after | Free Tier plan state PAID | **BLOCKED** until verified |
| **Cognito user pool** | sign-up/sign-in | Lite/Essentials: 10,000 MAU/mo free (50,000 only for pre-2024-11-21 accounts); Plus: none | email via Cognito default sender has a daily cap; SES/SNS cost if raised | new pool ⇒ 10k tier expected | **UNKNOWN → likely ELIGIBLE**; confirm on live pricing page |
| **RDS PostgreSQL** (db.t4g.micro, Single-AZ, 20 GB) | Track A DB | **Free plan only**: 6 months/credits, db.t3/t4g.micro | storage, backup, **public IPv4 $0.005/h (~$3.65/mo) if public**, VPC endpoints/NAT for Lambda reach | account is PAID ⇒ **credit-backed only, not free** | **BLOCKED** (not perpetual; needs owner risk approval) |
| **NAT Gateway** | Lambda-in-VPC egress | none | ~$0.045/h + data ≈ $33+/mo | no | **BLOCKED — never** |
| **Public IPv4** | any public address | EC2 750 h/mo (12-mo tier) | $0.005/h each | no | BLOCKED |
| **DynamoDB** (on-demand/provisioned) | Track B | always-free 25 GB + 25 RCU/25 WCU provisioned (verify) | PITR/backups, streams | likely | **UNKNOWN** (needs redesign, see below) |
| **S3** | opt-in exports only | 5 GB/12 months (Free Tier plan) | requests, egress | PAID plan ⇒ not free | UNKNOWN; not required for MVP → **not created** |
| **CloudFront** | via Amplify only | built in | egress overage | — | no standalone distribution |
| **Route 53** hosted zone | custom DNS | **none** | $0.50/zone/mo + queries; domain registration extra | no | **BLOCKED/deferred** (use Amplify default domain) |
| **EC2 t4g.micro + EBS** | fallback only | 750 h/mo for 12-mo tier | EBS, public IPv4, patching | PAID plan | **BLOCKED** (not needed) |
| **IAM** | roles | free | — | yes | ELIGIBLE |
| **CloudWatch** | Lambda logs only; no custom metrics | 5 GB logs ingest, 10 metrics (verify) | custom metrics/alarms bill | partially | UNKNOWN; keep retention ≤ 7 d |
| **Budgets** | alert only | first 2 budgets free (verify) | **not a cap** | — | allowed only with owner approval |
| **SSM Parameter Store (Standard)** | config/secrets | no charge for Standard params (verify) | KMS only if CMK | yes | ELIGIBLE (preferred) |
| **Secrets Manager** | — | 30-day trial | $0.40/secret/mo | credit-eligible, not free | **BLOCKED** (use SSM SecureString) |
| **SQS** | not used | 1M req/mo always-free | — | — | not needed (SQL leases are authoritative) |
| **AppSync** | not used | none perpetual | per-request/connection | — | **BLOCKED** (polling instead) |
| **EKS/ECS/Fargate/ALB/ECR** | not used | none | all bill | — | BLOCKED |

Sources: https://aws.amazon.com/rds/free/ · https://aws.amazon.com/cognito/pricing/ · https://aws.amazon.com/amplify/pricing/ · https://aws.amazon.com/api-gateway/pricing/ · https://aws.amazon.com/vpc/pricing/ · https://aws.amazon.com/free/free-tier-faqs/ . Items marked "verify" were **not** confirmed in this session.

## Architectural decision: Track A vs Track B

| | **Track A — PostgreSQL (RDS)** | **Track B — DynamoDB** |
|---|---|---|
| Engineering state | **Implemented and tested locally**: `infra/aws/sql`, `PostgresStore` (33 tests on real PG18) | Not started; needs a redesign (single-table design, conditional writes for lease/claim, GSI per workspace, sequence replay) |
| Free-ness | Credit-backed only on a PAID plan (**RDS is not perpetually free**); Lambda-to-RDS needs VPC ⇒ NAT/endpoint cost trap, or public RDS (forbidden) | Always-free capacity possible, still subject to usage and no NAT needed |
| Risk | Cost + networking | Functional parity (atomic lease claim, ordered events, tenant isolation without SQL/RLS) |

**Recommendation:** keep Track A as the implemented, tested reference. **Do not provision it** until the owner (1) confirms credit expiry in the console, (2) approves a bounded spend risk and (3) approves a Lambda↔RDS network design with **no NAT gateway**. This is an unsolved cost trap: a Lambda must sit inside the VPC to reach a private RDS, and then it has no internet path to fetch Cognito's JWKS (or reach SSM) without a NAT gateway or paid interface endpoints. The only NAT-free option on the table is to pin the pool's public JWKS as deployment configuration instead of fetching it at runtime (not implemented; needs a decision, see `docs/AWS_ARCHITECTURE.md`). If the goal is strict perpetual $0, Track B is the only candidate, and it is a separate, approved refactor — not started.

## Required owner decisions before any provisioning
1. Confirm credit **expiry date** and that spending may draw on the USD 99.99 credits (and what happens after).
2. Approve one region (suggested `us-east-1`; not yet approved).
3. Approve use of a **non-root** deployment identity (create an IAM Identity Center/role).
4. Choose Track A (credit-backed) or Track B (refactor).
5. Approve the exact resource inventory in `docs/AWS_DEPLOYMENT.md` and a teardown date.


## Next.js 16 / Amplify hard gate (rechecked 2026-11-21)
- Official AWS guide (https://docs.aws.amazon.com/amplify/latest/userguide/ssr-amplify-support.html): managed SSR for **Next.js 12–15**. A tracking thread (https://github.com/aws-amplify/amplify-js/issues/14600) says the *library* supports 16 but Amplify **Hosting** does not yet, with an unverified report that 16.1+ fails. **Status: BLOCKED. No Amplify deploy, no silent downgrade.**
- `npm run build` success proves nothing about Amplify. What *was* validated locally (no AWS): `ENIGMA_STANDALONE=1 next build` produces a self-contained Node server (`server.js`, ~44 MiB) in which the proxy redirect, Cognito cookie gate, same-origin check and BFF 401 behave correctly.
- **Proposal (not built, not priced as approved):** run that standalone server on Lambda via the AWS Lambda Web Adapter behind a Function URL, with static assets served by the same function. Cost/privacy consequences: every page and asset request is a Lambda invocation (request + duration billing, cold starts); adding CloudFront for caching adds another billed service; the Function URL is public at the edge. Alternative: pin Next.js 15.x (a deliberate, tested downgrade the owner must approve; not done).
