# AWS cost matrix and decision record — 2026-11-21

**Status: NO AWS RESOURCE EXISTS and none may be created until you approve an itemized plan (see `AWS_DEPLOYMENT.md`).** All numbers below are estimates from public list prices and stated assumptions, not a quote. Region used for every figure: **`us-east-1` (N. Virginia)** — *recommended, not yet approved by you.*

## 1. Account facts (read-only AWS CLI; sensitive values withheld)
| Item | Result | How determined |
|---|---|---|
| Plan | **PAID**, status ACTIVE | `aws freetier get-account-plan-state` |
| Credits remaining | **USD 99.99** | same |
| Account created | **2026-07-18** (about 4 months ago) | `aws account get-account-information` |
| **Credit expiry date** | **UNKNOWN — no API returns it.** AWS pages conflict: ≤12 months from sign-up (AWS blog) vs 6 months (FAQ). Inferred window: **2027-01-18 (6 mo) to 2027-07-18 (12 mo)**. Read the exact date in Billing console → Credits | inference only |
| Free Tier usage records | none | `aws freetier get-free-tier-usage` |
| Resources in us-east-1 | none (EC2, RDS, Amplify, Cognito, Lambda, CloudFormation, S3, Route 53 all 0) | read-only list calls |
| Free-plan features | **not applicable** — the account already upgraded to PAID. Offers tied to the Free *plan* (e.g. the RDS "Free plan", 6 months) are therefore **not assumed** | AWS RDS/Free Tier pages |
| MFA on account | enabled | `iam get-account-summary` |
| **Root access keys present** | **YES — security risk.** The CLI profile also authenticates as the **root user**. Before any deployment: create a least-privilege admin identity (IAM Identity Center) and delete the root access keys. Not done (no IAM changes permitted) | `iam get-account-summary`, `sts get-caller-identity` |
| Always-free allowances for this PAID account | **UNVERIFIED** (Lambda 1M req/400k GB-s, CloudWatch 5 GB, CloudFront 1 TB/10M req, DynamoDB 25 GB + 25 WCU/RCU, Cognito 10k MAU). Every estimate is therefore shown **twice**: LIST (no allowances, safe upper bound) and FREE (allowances applied) | pricing pages |

## 2. Verified unit prices (us-east-1, on-demand)
Read 2026-11-21 from the **AWS Price List API** (`aws pricing get-products`, read-only, no charge) unless noted.
| Item | Price | Source |
|---|---|---|
| Lambda requests | $0.20 / M | Price List `AWSLambda`; https://aws.amazon.com/lambda/pricing/ |
| Lambda duration x86 / arm64 | $0.0000166667 / $0.0000133334 per GB-s | same |
| Lambda response streaming | first 6 MB per request free, then $0.008 / GB | same |
| Lambda **Web Functions** (ARM only) | $0.30 / M requests; $0.0000276944 per active vCPU-s; $0.0000036667 per GB-s memory **while the environment is alive, including idle between requests** | same |
| Lambda Function URL | no separate charge listed on the pricing pages (**absence of a listing, not an explicit statement**) | https://aws.amazon.com/lambda/pricing/ |
| CloudFront (pay-as-you-go, US) | 1 TB + 10 M HTTPS req/mo "Always Free"; then $0.085/GB (to 10 TB) and $0.01 / 10k HTTPS req; origin fetch from AWS origins free | https://aws.amazon.com/cloudfront/pricing/ |
| CloudFront flat-rate **Free** plan | $0: 1M req, 100 GB; **eligibility for a PAID-plan account UNKNOWN** (one AWS guide says Free-Tier accounts cannot use flat-rate plans) | same |
| CloudWatch Logs | ingest $0.50/GB, storage $0.03/GB-mo, 5 GB/mo free | Price List `AmazonCloudWatch` |
| Cognito user pools | Lite $0.0055/MAU, Essentials $0.015/MAU; 10,000 MAU/mo free (not expiring) for new accounts | Price List `AmazonCognito`; https://aws.amazon.com/cognito/pricing/ |
| RDS PostgreSQL Single-AZ | db.t4g.micro $0.016/h; db.t3.micro $0.018/h; gp3 $0.115/GB-mo (min 20 GB); backup beyond free allocation $0.095/GB-mo | Price List `AmazonRDS` |
| DynamoDB | on-demand $0.625/M write units, $0.125/M read units; storage $0.25/GB-mo after 25 GB free; provisioned $0.00065/WCU-h, $0.00013/RCU-h after 25+25 free; PITR $0.20/GB-mo | Price List `AmazonDynamoDB` |
| NAT gateway | $0.045/h (= **$32.85/mo**) + $0.045/GB | Price List `AmazonEC2` |
| Public IPv4 | $0.005/h (= $3.65/mo) each | Price List `AmazonVPC` |
| Interface VPC endpoint | $0.01/h per AZ (= $7.30/mo) + $0.01/GB | Price List `AmazonVPC` |
| S3 Standard | $0.023/GB-mo; $0.005 / 1k PUT | Price List `AmazonS3` |
| SSM Parameter Store (standard) | no additional charge | https://aws.amazon.com/systems-manager/pricing/ |
| Secrets Manager | $0.40/secret/mo + $0.05/10k calls (from page examples; verify) | https://aws.amazon.com/secrets-manager/pricing/ |

## 3. Next.js 16 hosting — requirements and the two Lambda options
Amplify Hosting remains **BLOCKED** (AWS documents SSR for Next.js 12–15 only; https://docs.aws.amazon.com/amplify/latest/userguide/ssr-amplify-support.html).

**Features Enigma actually uses** (from the code): App Router with `force-dynamic` SSR; route handlers (GET/POST BFF with request bodies ≤128 KiB); `proxy.ts` (Next 16 Node-runtime middleware); `cookies()` with httpOnly `Set-Cookie` from route handlers; `next/font/google` (resolved at build time). **Not used:** ISR/revalidation, `next/image` optimization, server actions, edge runtime, streaming SSR dependence, `public/` assets of significance.

| Aspect | **A. Lambda Web Adapter** on a normal (Event) Lambda | **B. Lambda Web Functions** (launched 2026-10-12) |
|---|---|---|
| What it is | Open-source extension that turns Lambda events into HTTP calls to a port-listening server (Next.js `standalone`) | New Lambda form factor: your Node server listens on a port; multi-request concurrency per environment |
| Runtime | any (Node for Next) | **Node.js only today** (so **not** usable for the Python API) |
| Billing | requests + GB-s (ARM $0.0000133334) | requests + **active CPU** + **memory for the environment's whole life** (idle time included); no free tier documented |
| Documented Next.js guidance | The adapter README and an AWS compute blog list Next.js among supported web frameworks (page bodies partly unread) | **CONFLICTING / UNVERIFIED**: one search summary attributes a "Next.js standalone needs a small `index.js` setting host and port" note to AWS's Web Functions guide; another search found no official Next.js guide (only a community CDK issue). The blog body would not load, so neither could be confirmed |
| Payload / duration limits | buffered response 6 MB, request 6 MB; Function URL timeout up to 15 min; streaming up to 200 MB (first 6 MB uncapped, then 2 MB/s) | **NOT FOUND** in the pages read (blog bodies would not load) |
| Cold starts | per Event invocation environment | fewer (one environment serves many requests) |
| Maturity | established | **about 6 weeks old** at the time of writing |
| Local validation done | Standalone bundle builds and runs under Node; proxy/auth/BFF behaviour verified (see TEST_REPORT) | none possible |
| **Status** | **Recommended for the web tier** | **Watch**; revisit once limits and a Next.js guide are published |

**Deployment limitations found by analysis (and fixed in code):** behind CloudFront → Function URL the origin sees the *Function URL's* `Host`. (1) The Cognito same-origin CSRF check compared `Origin` to `Host` and would have returned 403 for real users — fixed by an explicit `ENIGMA_PUBLIC_HOST` allow-list entry. (2) `proxy.ts` redirected to `/sign-in` using the request URL, which would expose the Function URL — fixed with `ENIGMA_PUBLIC_ORIGIN`. The standalone bundle excludes `.next/static` and `public/`; they must be copied into the package. CloudFront must not forward the viewer `Host` to a Function URL origin. **Not verifiable without a deployment:** multi-cookie `Set-Cookie` pass-through, adapter readiness checks, real cold-start latency.

## 4. Workload model and assumptions
Everything is regenerated by `/usr/local/bin/python3.13 scripts/aws_cost_model.py` (offline; edit the `ASSUMPTIONS` block to change it; guarded by `tests/test_aws_cost_model.py`).
- Connector defaults: `claim` poll **1 s**, heartbeat 10 s, telemetry upload 5 s ⇒ 4,680 API requests per Kaggle GPU-hour. A 5 s idle claim poll would cut the connector's share by ~62 % (not implemented; worth doing before deployment).
- Browser: 3 metric hooks polled every 5 s while a dashboard is open; chat polling every 0.4 s while a reply streams. **Every browser call is two Lambda invocations** (browser → Next BFF → API).
- API Lambda: Python 3.13, 512 MB, ~50 ms per request, reserved concurrency 5. Web Lambda: Next standalone, ARM64, 1 GB, 60 ms BFF / 150 ms SSR / 15 ms asset; CloudFront caches 90 % of the ~15 static assets per page view.
- Logs: ~700 bytes per invocation, 7-day retention. No cold-start/init billing modelled. Timings are **assumptions to be measured** before approval.
- Workload sizes: *low* = one developer, light use; *moderate* = small team; *high* = development ceiling, equal to Kaggle's ~30 GPU-hours/week (~130 h/mo). **This is a dev/demo control plane, not production traffic.**

## 5. Itemized monthly estimates (USD, us-east-1)
| Workload | Kaggle GPU h/mo | dashboard-open h | chat-streaming h | page views | MAU | API invocations | Web invocations (with CDN) |
|---|---:|---:|---:|---:|---:|---:|---:|
| low | 10 | 10 | 0.5 | 500 | 2 | 72,900 | 27,350 |
| moderate | 60 | 60 | 3 | 5,000 | 5 | 437,400 | 169,100 |
| high | 130 | 130 | 10 | 30,000 | 25 | 979,200 | 445,800 |

**low** — USD / month, us-east-1

| Line item | LIST (no free tier) | With documented free tiers |
|---|---:|---:|
| API Lambda (Python 3.13, 512 MB) | 0.04 | 0.00 |
| Web Lambda (Next 16 via Web Adapter, ARM64, 1 GB) | 0.03 | 0.00 |
| Lambda Function URLs (2) | 0.00 | 0.00 |
| CloudFront (requests + transfer out) | 0.11 | 0.00 |
| S3 (deploy artifacts ~0.1 GB) | 0.00 | 0.00 |
| CloudWatch Logs (7-day retention) | 0.04 | 0.00 |
| Cognito (Lite tier) | 0.01 | 0.00 |
| *Subtotal common to both tracks* | *0.23* | *0.00* |
| **Track A** RDS db.t4g.micro + 20 GB gp3 (private, no NAT/IPv4/endpoints) | 13.98 | 13.98 |
| **Track A total** | **14.21** | **13.98** |
| Track A if a NAT gateway were needed (+32.85) | 47.06 | 46.83 |
| **Track B** DynamoDB (design only) | 0.15 | 0.00 |
| **Track B total** | **0.37** | **0.00** |
| Alternative web tier on Lambda Web Functions (replaces the Web Lambda line; list) | 0.51 (req 0.01 + CPU 0.01 + mem 0.50) | n/a |

**moderate** — USD / month, us-east-1

| Line item | LIST (no free tier) | With documented free tiers |
|---|---:|---:|
| API Lambda (Python 3.13, 512 MB) | 0.27 | 0.00 |
| Web Lambda (Next 16 via Web Adapter, ARM64, 1 GB) | 0.17 | 0.00 |
| Lambda Function URLs (2) | 0.00 | 0.00 |
| CloudFront (requests + transfer out) | 0.84 | 0.00 |
| S3 (deploy artifacts ~0.1 GB) | 0.00 | 0.00 |
| CloudWatch Logs (7-day retention) | 0.22 | 0.00 |
| Cognito (Lite tier) | 0.03 | 0.00 |
| *Subtotal common to both tracks* | *1.53* | *0.01* |
| **Track A** RDS db.t4g.micro + 20 GB gp3 (private, no NAT/IPv4/endpoints) | 13.98 | 13.98 |
| **Track A total** | **15.51** | **13.99** |
| Track A if a NAT gateway were needed (+32.85) | 48.36 | 46.84 |
| **Track B** DynamoDB (design only) | 0.87 | 0.00 |
| **Track B total** | **2.40** | **0.01** |
| Alternative web tier on Lambda Web Functions (replaces the Web Lambda line; list) | 3.97 (req 0.05 + CPU 0.06 + mem 3.86) | n/a |

**high** — USD / month, us-east-1

| Line item | LIST (no free tier) | With documented free tiers |
|---|---:|---:|
| API Lambda (Python 3.13, 512 MB) | 0.60 | 0.05 |
| Web Lambda (Next 16 via Web Adapter, ARM64, 1 GB) | 0.45 | 0.04 |
| Lambda Function URLs (2) | 0.00 | 0.00 |
| CloudFront (requests + transfer out) | 3.79 | 0.00 |
| S3 (deploy artifacts ~0.1 GB) | 0.00 | 0.00 |
| CloudWatch Logs (7-day retention) | 0.51 | 0.01 |
| Cognito (Lite tier) | 0.14 | 0.00 |
| *Subtotal common to both tracks* | *5.50* | *0.09* |
| **Track A** RDS db.t4g.micro + 20 GB gp3 (private, no NAT/IPv4/endpoints) | 13.98 | 13.98 |
| **Track A total** | **19.48** | **14.07** |
| Track A if a NAT gateway were needed (+32.85) | 52.33 | 46.92 |
| **Track B** DynamoDB (design only) | 1.94 | 0.00 |
| **Track B total** | **7.44** | **0.09** |
| Alternative web tier on Lambda Web Functions (replaces the Web Lambda line; list) | 9.95 (req 0.13 + CPU 0.18 + mem 9.64) | n/a |

Reading the tables: the *LIST* column assumes **no** free allowance applies (the safe bound for a PAID account). The *FREE* column is only valid if the always-free allowances really apply to this account. Track B's FREE figure uses DynamoDB *provisioned* free capacity (25 WCU/25 RCU); sustained dashboard polling needs about 18.0 RCU/s per open dashboard, so throttling risk exists and capacity must be tuned.

**Credit runway if Track A is deployed:** USD 99.99 ÷ about USD 14.2–19.5/month ≈ **5–7 months**, but the credits may expire as early as ~2027-01-18 (about 1.9 months of use ≈ USD 27–37 spent; the rest of the credits would lapse unused). After credits end, RDS bills the card at ≈ USD 14/month **until it is torn down**.

## 6. Track A (RDS PostgreSQL) vs Track B (DynamoDB)
| | **A — RDS PostgreSQL** | **B — DynamoDB** |
|---|---|---|
| Code state | **Implemented**: `infra/aws/sql`, `PostgresStore`; 33 store tests + 61 SQL checks on real PostgreSQL 18 | **Not started.** Design only |
| Monthly database cost | **≈ USD 13.98** fixed (instance 11.68 + 20 GB 2.30) even when idle | USD 0–2 (list), ≈0 with free capacity |
| Free forever? | **No.** Credit-funded only; ends with credits | Plausible within DynamoDB's monthly free tier at dev scale; never "guaranteed" |
| Networking | must sit in a VPC; **no outbound internet** without NAT (+$32.85/mo). NAT-free design: pinned Cognito JWKS (implemented, `COGNITO_JWKS_JSON`) + DSN in encrypted Lambda env var (no Secrets Manager, no interface endpoints) | no VPC; Lambda keeps internet; no NAT/IPv4/endpoints |
| Operational risk | Cognito key rotation breaks auth until the pinned JWKS is updated; a forgotten RDS keeps billing | throttling if capacity mis-sized; items ≤400 KB; query pages ≤1 MB; TTL deletes lag up to ~48 h |
| Semantics | native transactions, `SKIP LOCKED`, constraints, SQL functions already proven | must re-create atomic claim, ordering, idempotency, caps, uniqueness with conditional writes/transactions |

**Decision rule you set:** the PostgreSQL code stays untouched until a DynamoDB store passes *equivalent* tests. The gate for Track B (all must pass against a DynamoDB emulator or a dedicated test table, with **no** production data):
1. **Tenancy:** the 27 store-agnostic API tests (`test_jobs`, `test_auth_and_tenancy`, `test_route_guards`, `test_telemetry`) re-run via the same fixture-override technique as `tests/api/test_postgres_store.py`; plus two-tenant 404 matrix. No RLS/LeadingKeys equivalent exists for a single Lambda role, so isolation stays application-layer (same as Track A today).
2. **Job leasing:** conditional `UpdateItem` on `(status, lease_expires_at)` over a strongly-consistent per-runtime pending partition (GSIs are eventually consistent); 8-thread race test must produce exactly one winner (mirrors `test_concurrent_claims_hand_the_job_to_exactly_one_worker`); lease expiry/reclaim and attempt cap with an injectable clock (mirrors `test_lease_expiry_reclaim_and_lockout_against_real_sql`).
3. **Event ordering:** `TransactWriteItems` writing `(job, seq)` with `attribute_not_exists` plus `last_seq = seq-1` condition on the job; duplicate replay, gap rejection and `expected_seq` semantics (mirrors `test_duplicate_and_out_of_order_events`); 2× write cost for transactional writes is included in the estimate only approximately.
4. **Idempotency:** conditional put on `IDEM#workspace#key`, `(runtime, batch_id)` telemetry batches, final assistant message exactly once.
5. **Caps and retention:** per-job event/text caps and per-user/runtime active-job caps via counters; TTL plus a prune task, with the TTL lag documented.
6. **Append-only audit:** IAM policy denying update/delete on audit items.
Tooling needed (needs your approval to install): DynamoDB Local or `moto`, and `boto3`.

## 7. Per-service status (supersedes the earlier matrix)
Legend: `VERIFIED_PRICE` = unit price confirmed from the Price List API; `CREDIT_ONLY` = would be paid from credits then card; `UNKNOWN`; `BLOCKED`.
| Service | Status | Note |
|---|---|---|
| Lambda (API + web) | VERIFIED_PRICE; free tier UNKNOWN for this account | pennies at all workloads |
| Lambda Function URL | UNKNOWN (no charge listed) | public at the edge; app-level auth mandatory |
| CloudFront (pay-as-you-go) | VERIFIED_PRICE; free tier UNKNOWN | only needed to cache static assets; <$4/mo list at the ceiling |
| Cognito Lite | VERIFIED_PRICE; free 10k MAU | choose **Lite** (password sign-in is all that is used); new pools default to Essentials at $0.015/MAU |
| CloudWatch Logs | VERIFIED_PRICE | 7-day retention, no custom metrics/alarms |
| S3 | VERIFIED_PRICE | only for code artifacts (~$0.00) |
| RDS PostgreSQL | CREDIT_ONLY | ≈$13.98/mo fixed |
| DynamoDB | VERIFIED_PRICE; code NOT BUILT | |
| NAT gateway / public IPv4 / interface endpoints / Secrets Manager | **BLOCKED (avoid)** | $32.85 / $3.65 / $7.30 per AZ / $0.40 per month |
| Amplify Hosting | **BLOCKED** | Next.js 16 not documented |
| Lambda Web Functions | UNKNOWN | no Next.js guide, limits not found, no free tier found |
| Route 53 / custom domain / WAF / EC2 / ALB / ECS / EKS / AppSync | **BLOCKED** | not needed; CloudFront default domain suffices |

## 8. Account-specific unknowns and remaining blockers
1. Credit **expiry date** (Billing console only). 2. Whether always-free allowances apply to this PAID account. 3. Your **approval** of region, Track, resource list and a spending ceiling. 4. A **non-root** deployment identity (and removal of root access keys). 5. Measured Lambda timings/cold starts (assumed here). 6. Lambda Web Adapter behaviour for Next 16 on real Lambda (cookies, readiness). 7. If Track B: approval to install DynamoDB Local/`moto`+`boto3` and the build/parity work. 8. Kaggle acceptance and the browser→AWS→Kaggle test need a deployed HTTPS API.

## 9. Sources
- AWS Price List API (queried 2026-11-21): https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/price-changes.html (service codes `AWSLambda`, `AmazonRDS`, `AmazonDynamoDB`, `AmazonCognito`, `AmazonCloudWatch`, `AmazonEC2`, `AmazonVPC`, `AmazonS3`)
- https://aws.amazon.com/lambda/pricing/ · https://aws.amazon.com/lambda/lambda-web-functions/ · https://aws.amazon.com/about-aws/whats-new/2026/10/aws-lambda-web-functions/ · https://aws.amazon.com/blogs/compute/exploring-aws-lambda-web-functions-for-building-web-applications/ · https://aws.amazon.com/blogs/compute/using-response-streaming-with-aws-lambda-web-adapter-to-optimize-performance/ · https://github.com/aws/aws-lambda-web-adapter
- https://aws.amazon.com/cloudfront/pricing/ · https://aws.amazon.com/cognito/pricing/ · https://aws.amazon.com/rds/free/ · https://aws.amazon.com/rds/pricing/ · https://aws.amazon.com/dynamodb/pricing/provisioned/ · https://aws.amazon.com/vpc/pricing/ · https://aws.amazon.com/systems-manager/pricing/ · https://aws.amazon.com/secrets-manager/pricing/ · https://aws.amazon.com/cloudwatch/pricing/
- https://aws.amazon.com/free/free-tier-faqs/ · https://aws.amazon.com/blogs/aws/aws-free-tier-update-new-customers-can-get-started-and-explore-aws-with-up-to-200-in-credits/ · https://docs.aws.amazon.com/amplify/latest/userguide/ssr-amplify-support.html · https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html · https://github.com/aws-amplify/amplify-js/issues/14600 · https://github.com/cdklabs/cdk-nextjs/issues/306

**Not read in full:** two AWS compute blog bodies would not load through the fetch tool, so Web Functions limits (timeout, payload, architectures) are reported as NOT FOUND rather than guessed.
