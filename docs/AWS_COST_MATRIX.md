# AWS cost matrix and decision record — 2026-11-21

> **Compute boundary (skills.md §16, highest precedence):** AWS hosts **only** the CPU control plane (web UI, FastAPI, auth, database, telemetry history). **No AWS GPU or accelerator of any kind** (EC2 G/P/DL/Trn/Inf, SageMaker, Bedrock, GPU containers) is planned, priced as intended infrastructure, or permitted — not even if credits could pay for it. All Stokastisk/vLLM inference runs **only** on your user-launched Kaggle 2× Tesla T4. When Kaggle is off, Enigma shows **offline** and keeps history; nothing falls back to AWS. Enforced by `tests/test_no_aws_gpu.py` and by the deny statements in `docs/AWS_ROOT_KEY_REMEDIATION.md` §5.

**Status: NO AWS RESOURCE EXISTS and none may be created until you approve an itemized plan (see `AWS_DEPLOYMENT.md`).** All numbers below are estimates from public list prices and stated assumptions, not a quote. Region used for every figure: **`us-east-1` (N. Virginia)** — *recommended, not yet approved by you.*

> **2026-11-21 update:** added the CloudFront-vs-Function-URL comparison, a measured cost for the real-DynamoDB parity run (§6), the Next.js Lambda package findings (§3) and the approval manifest (`infra/aws/resource-manifest.yaml`, unapproved). No AWS call was made to produce any of it: AWS access is root-only, so all account facts below are from earlier read-only checks.

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

**Deployment limitations found by analysis (and fixed in code):** behind CloudFront → Function URL the origin sees the *Function URL's* `Host`. (1) The Cognito same-origin CSRF check compared `Origin` to `Host` and would have returned 403 for real users — fixed by an explicit `ENIGMA_PUBLIC_HOST` allow-list entry. A live local test then showed that trusting `X-Forwarded-Host` let a spoofed header pass the check, so **`X-Forwarded-Host` is no longer trusted at all** (only `Host` and the configured public host). (2) `proxy.ts` redirected to `/sign-in` using the request URL, which would expose the Function URL — fixed with `ENIGMA_PUBLIC_ORIGIN`. The standalone bundle excludes `.next/static` and `public/`; they must be copied into the package. CloudFront must not forward the viewer `Host` to a Function URL origin. **Not verifiable without a deployment:** multi-cookie `Set-Cookie` pass-through, adapter readiness checks, real cold-start latency.

**Lambda package findings (local, 2026-11-21).** Building the Next 16 standalone output initially bundled `sharp-linux-x64` (a native x86_64 binary plus libvips, 44 MB) for image optimisation that Enigma does not use. Setting `images.unoptimized` and excluding `sharp` from file tracing (only for the opt-in `ENIGMA_STANDALONE=1` build) gives a **22.8 MB package with zero native binaries**, so it is **architecture independent**: ARM64 (cheaper per GB-second) is safe. `scripts/validate_nextjs_lambda.py` (27 checks) then ran the real server behind a CloudFront-style proxy that rewrites `Host`: SSR, immutable cacheable assets, redirects staying on the public origin, BFF 401, CSRF (including `X-Forwarded-Host` spoofing), two `Set-Cookie` headers surviving with HttpOnly/Secure/SameSite, no local secret in the package, ≈ 120 MB resident memory (sizes the Lambda memory setting; 1 GB in the model is generous). It found two real defects, both fixed: the middleware's 401 and redirect lacked `Cache-Control: no-store` (a CDN could replay an auth decision), and the earlier `X-Forwarded-Host` trust. **Still NOT proven:** the real Lambda Web Adapter, Function URL payload translation, real CloudFront, Lambda cold starts, the 6 MB buffered-response limit in practice. Node 24 was the local runtime; the Lambda runtime identifier must be checked against AWS's current list at deploy time.

## 4. Workload model and assumptions
Everything is regenerated by `/usr/local/bin/python3.13 scripts/aws_cost_model.py` (offline; edit the `ASSUMPTIONS` block to change it; guarded by `tests/test_aws_cost_model.py`).
- Connector defaults: `claim` poll **1 s**, heartbeat 10 s, telemetry upload 5 s ⇒ 4,680 API requests per Kaggle GPU-hour. An optional **idle back-off is now implemented** (`ENIGMA_CLAIM_POLL_IDLE_S`, default off = unchanged): the poll ramps to the idle ceiling after 30 s without activity and returns to fast after any job. At a 5 s ceiling it removes ~72 % of claim polls (~35 % of all API invocations) but a prompt submitted while idle waits up to ~5 s (simulated bound; real latency must be measured on a deployed stack). **The dollar saving is small (USD 0.03–0.37/month in the model below)**, so it is a headroom/free-tier measure, not a cost lever.
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

**Effect of the idle claim back-off** (`claim_poll_idle_s = 5`, 10 % of Kaggle time inside the 30 s hot window; LIST prices)

| Workload | API invocations: default → tuned | Track A total: default → tuned | Track B total: default → tuned |
|---|---:|---:|---:|
| low | 72,900 → 46,980 | 14.21 → 14.18 | 0.37 → 0.35 |
| moderate | 437,400 → 281,880 | 15.51 → 15.36 | 2.40 → 2.23 |
| high | 979,200 → 642,240 | 19.48 → 19.15 | 7.44 → 7.07 |

**Web tier: CloudFront vs a raw Function URL** (web-tier cost only, LIST prices, no free tier; the API, database, auth are identical)

| Workload | Web invocations: CloudFront → raw | Web tier: CloudFront (list) | Web tier: CloudFront (its free tier applied) | Web tier: raw Function URL |
|---|---:|---:|---:|---:|
| low | 27,350 → 34,100 | 0.15 | 0.04 | 0.04 |
| moderate | 169,100 → 236,600 | 1.07 | 0.23 | 0.28 |
| high | 445,800 → 850,800 | 4.40 | 0.61 | 0.92 |

**CloudFront vs raw Function URL (corrected 2026-11-21).** Earlier text leaned towards keeping CloudFront because it removes about half of the web-tier Lambda invocations. Priced properly the picture is different: at **list prices (no free tier)** a **raw Function URL is cheaper** at every workload (≈ $0.04 / $0.28 / $0.92 a month versus ≈ $0.15 / $1.07 / $4.40 with CloudFront), because CloudFront's transfer price outweighs the Lambda invocations it saves. **If CloudFront's always-free allowance (1 TB / 10 M requests) applies** to this account, CloudFront becomes cheaper at moderate and high load ($0.23 / $0.61) and about equal at low load. The money is single-digit dollars either way, so the choice rests on **non-price** factors: CloudFront gives cacheable immutable assets (lower latency), a stable public hostname independent of the Function URL, and a place to attach WAF later (paid); a raw Function URL has **fewer moving parts** (no `Host` rewriting, so `ENIGMA_PUBLIC_HOST`/`ENIGMA_PUBLIC_ORIGIN` are unnecessary) and a public `*.lambda-url.<region>.on.aws` hostname. **Recommendation: start with the raw Function URL** (cheapest at list price, simplest, fully validated by the same security checks) and add CloudFront only if latency, a custom domain or WAF is actually needed. This is a decision for you; neither is deployed.

**Polling note:** the back-off table above shows the saving is real but small in dollars (USD 0.03–0.37/month); its main value is lower Lambda/CloudWatch volume. It trades pickup latency for traffic, so it ships **off by default**.

Reading the tables: the *LIST* column assumes **no** free allowance applies (the safe bound for a PAID account). The *FREE* column is only valid if the always-free allowances really apply to this account. Track B's FREE figure uses DynamoDB *provisioned* free capacity (25 WCU/25 RCU); sustained dashboard polling needs about 18.0 RCU/s per open dashboard, so throttling risk exists and capacity must be tuned.

**Credit runway if Track A is deployed:** USD 99.99 ÷ about USD 14.2–19.5/month ≈ **5–7 months**, but the credits may expire as early as ~2027-01-18 (about 1.9 months of use ≈ USD 27–37 spent; the rest of the credits would lapse unused). After credits end, RDS bills the card at ≈ USD 14/month **until it is torn down**.

## 6. Track A (RDS PostgreSQL) vs Track B (DynamoDB)
| | **A — RDS PostgreSQL** | **B — DynamoDB** |
|---|---|---|
| Code state | **Implemented**: `infra/aws/sql`, `PostgresStore`; 33 store tests + 61 SQL checks on real PostgreSQL 18 | **Implemented and parity-tested against an emulator only** (`store/dynamodb.py`, 70+ tests on moto, incl. the whole existing API suite). **Not run against real DynamoDB.** |
| Monthly database cost | **≈ USD 13.98** fixed (instance 11.68 + 20 GB 2.30) even when idle | USD 0–2 (list), ≈0 with free capacity |
| Free forever? | **No.** Credit-funded only; ends with credits | Plausible within DynamoDB's monthly free tier at dev scale; never "guaranteed" |
| Networking | must sit in a VPC; **no outbound internet** without NAT (+$32.85/mo). NAT-free design needs the API to verify Cognito tokens offline: an optional **pinned JWKS seed** (`COGNITO_JWKS_JSON`, Track A only) plus the DSN in an encrypted Lambda env var (no Secrets Manager, no interface endpoints). Key rotation then needs an operator update unless the network is reachable | no VPC; Lambda keeps normal internet, so Cognito keys are fetched, cached and **refreshed automatically** (throttled; see `AWS_SECURITY.md`); no NAT/IPv4/endpoints |
| Operational risk | Cognito key rotation breaks auth until the pinned JWKS is updated; a forgotten RDS keeps billing | throttling if capacity mis-sized; items ≤400 KB; query pages ≤1 MB; TTL deletes lag up to ~48 h |
| Semantics | native transactions, `SKIP LOCKED`, constraints, SQL functions already proven | must re-create atomic claim, ordering, idempotency, caps, uniqueness with conditional writes/transactions |

**Decision rule you set:** the PostgreSQL code stays untouched until a DynamoDB store passes *equivalent* tests. Status of that gate (all on **moto**, an in-process emulator; **no AWS account, credential or network was used** — a session-wide guard blocks non-loopback sockets and tests run with fake credentials):

| Requirement | Evidence (tests in `tests/api/test_dynamodb_store.py`) | Result |
|---|---|---|
| Tenancy | the existing store-agnostic API suite (jobs, leases, tenancy, telemetry, route guards) re-run on `DynamoStore` with the same fake clock; plus a store-level two-tenant matrix (reads, cross-workspace submit/cancel/claim/append, global slug uniqueness, atomic owner-workspace cap) | PASS (emulator) |
| Atomic claim / leasing | 8-thread race: exactly one winner; lease expiry, re-claim, old-owner lock-out and attempt cap via the injectable clock | PASS (emulator), 6/6 repeat runs |
| Ordered events | 4 threads replaying the same 20-event batch: each event accepted exactly once, strictly ordered, `last_event_seq` correct; gap rejection and `expected_seq` | PASS (emulator) |
| Idempotency | concurrent identical submit key → one job and one user message; completion exactly once; telemetry batch replay; partial-write replay | PASS (emulator) |
| Caps / retention | per-user submit cap holds under an 8-thread race; event-cap failure keeps accepted events; exact-age `prune()` spares live jobs; `ttl` attribute set for DynamoDB expiry | PASS (emulator) |
| Failure injection | transient throttling retried; permanent failure → 503 with no row data; failed completion transaction is atomic and the retry finishes exactly once | PASS (emulator) |
| Consistency | no GSI; every read asserted `ConsistentRead=True`; only the configured table is touched; no schema operations | PASS |
| Durability | a fresh store instance (cold Lambda) sees the data; runtime shows offline while history persists | PASS (emulator) |
| Row shape | key sets identical to the in-memory reference for every returned row type | PASS |

**Real-table run:** a guarded, unit-tested harness exists (`tests/live_ddb_guard.py`, `infra/aws/dynamodb-parity.yaml`) but **has not been run**; measured demand of the 78-test suite is ≈ 5,700 write units and ≈ 2,900 read units (< $0.01 at list price); see `docs/AWS_DYNAMODB_REAL_PARITY.md`. Findings so far from strengthening the suite: a real bug (`BatchGetItem` silently truncated results after repeated `UnprocessedKeys`; now retried with backoff and raises instead), and a latent chart accessibility bug unrelated to AWS.

**What this does NOT prove.** moto does not model real throttling, partition limits, transaction-conflict timing or eventual consistency. A real-table run (needs your approval, a few cents of capacity) is still required before Track B is called equivalent. Two emulator caveats surfaced while building it: moto's `TransactWriteItems` is not thread-safe (it crashed on concurrent calls, so race tests serialise each *call*, which models DynamoDB's per-call atomicity while still interleaving *between* calls), and moto returns empty index lists where DynamoDB omits them. Documented differences from SQL: the clock is the Lambda's rather than the database's; `expire_jobs(None)` global sweep is not supported (expiry is per runtime on claim and per job on read); concurrent telemetry batches racing on identical keys may double-count.

**Recommendation unchanged in direction, firmer in substance:** Track B is now the lower-cost path with working code. Do not announce migration complete until a real-table parity run passes. Track A remains available and untouched.

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
1. Credit **expiry date** (Billing → Credits only; no API returns it). 2. Whether always-free allowances apply to this PAID account. 3. Your **approval** of region, track, itemized resources and a dollar ceiling. 4. A **non-root** deployment identity: only root credentials exist today and root access keys are present (`AWS_ROOT_KEY_REMEDIATION.md`). 5. Measured Lambda timings and cold starts (assumed here). 6. Lambda Web Adapter behaviour for Next 16 on real Lambda (cookies, readiness). 7. A **real-DynamoDB** parity run (emulator evidence only so far). 8. Kaggle acceptance and the browser → AWS → Kaggle test need a deployed HTTPS API and your launching the notebook.

## 9. Sources
- AWS Price List API (queried 2026-11-21): https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/price-changes.html (service codes `AWSLambda`, `AmazonRDS`, `AmazonDynamoDB`, `AmazonCognito`, `AmazonCloudWatch`, `AmazonEC2`, `AmazonVPC`, `AmazonS3`)
- https://aws.amazon.com/lambda/pricing/ · https://aws.amazon.com/lambda/lambda-web-functions/ · https://aws.amazon.com/about-aws/whats-new/2026/10/aws-lambda-web-functions/ · https://aws.amazon.com/blogs/compute/exploring-aws-lambda-web-functions-for-building-web-applications/ · https://aws.amazon.com/blogs/compute/using-response-streaming-with-aws-lambda-web-adapter-to-optimize-performance/ · https://github.com/aws/aws-lambda-web-adapter
- https://aws.amazon.com/cloudfront/pricing/ · https://aws.amazon.com/cognito/pricing/ · https://aws.amazon.com/rds/free/ · https://aws.amazon.com/rds/pricing/ · https://aws.amazon.com/dynamodb/pricing/provisioned/ · https://aws.amazon.com/vpc/pricing/ · https://aws.amazon.com/systems-manager/pricing/ · https://aws.amazon.com/secrets-manager/pricing/ · https://aws.amazon.com/cloudwatch/pricing/
- https://aws.amazon.com/free/free-tier-faqs/ · https://aws.amazon.com/blogs/aws/aws-free-tier-update-new-customers-can-get-started-and-explore-aws-with-up-to-200-in-credits/ · https://docs.aws.amazon.com/amplify/latest/userguide/ssr-amplify-support.html · https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html · https://github.com/aws-amplify/amplify-js/issues/14600 · https://github.com/cdklabs/cdk-nextjs/issues/306

**Not read in full:** two AWS compute blog bodies would not load through the fetch tool, so Web Functions limits (timeout, payload, architectures) are reported as NOT FOUND rather than guessed.
