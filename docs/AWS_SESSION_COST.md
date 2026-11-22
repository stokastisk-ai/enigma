# Session-based cost, activation behaviour and spend controls (2026-11-22)

Scope: ~1-2 h personal Kaggle sessions, a handful per month. **Nothing here is deployed or approved.** All figures come from `scripts/aws_session_cost.py` (offline, source-controlled, tested). They are LIST-price model estimates, **not quotes**; request counts and DynamoDB units are estimated from reading the code and must be measured on the first approved deployment.

## 1. Expected monthly charges (Path A, us-east-1, LIST prices; "free" = documented always-free allowances, applicability to this PAID-plan account UNVERIFIED)
| Scenario | LIST | With free tiers |
|---|---:|---:|
| 1 session x 1 h | $0.04 | $0.02 |
| 1 session x 2 h | $0.08 | $0.04 |
| **10 sessions x 1 h** | **$0.35** | $0.19 |
| **10 sessions x 2 h** | **$0.68** | $0.37 |
| Idle month (no session, 10 history visits) | $0.01 | $0.00 |

Breakdown for 10 x 2 h (LIST): Lambda $0.142 · DynamoDB reads/writes $0.366 · DynamoDB storage $0.011 · CloudWatch Logs (7-day) $0.068 · data transfer out $0.088 · Cognito Lite $0.0055 (1 MAU, only if the 10k free tier does not apply) · S3 artifact $0.002.
**Idle floor** (no sessions): S3 artifact + Cognito MAU + a few history visits, about **$0.01/month**; Lambda, DynamoDB (on-demand) and logs cost nothing while nothing sends requests. DynamoDB storage and logs that remain after a session are retained for their TTL (events 48 h, telemetry 14 d, logs 7 d) and cost fractions of a cent.
Assumptions that move the numbers most: dashboard open for the **whole** session (worst case), ~12 prompts/hour at ~10 s of streaming each, 5 page views/session with every asset served by the web Lambda (no CDN). Charges not modelled: Lambda cold-start init time, requests from other people, the Function URL being scanned by bots (see section 4), CloudWatch Logs Insights, anything created outside this manifest.

Spend ceiling suggestion for the first approval: **$5/month hard-reviewed, $2 notification budget** (a Budget alert notifies; it does not cap spend). The model's worst reasonable month is under $1.

## 2. Activation and deactivation (no permanent worker anywhere)
| Event | What happens | AWS charge |
|---|---|---|
| No session | Lambdas and DynamoDB are idle. Nothing polls. A visitor opening the console sees history and an offline banner; the runtime list is refreshed every 30 s only while a tab is visible. | $0 compute |
| Kaggle notebook starts connector | `register` -> heartbeat every 10 s, telemetry every 5 s, job claim: **1 s for the first 30 s and after every prompt, backing off geometrically to 5 s** (`claim_poll_idle_s=5`, default since this session), jittered; errors back off exponentially (never under 2 s). | per-request only |
| Heartbeat age <= 30 s | UI "online"; chat enabled; dashboards poll every 5 s. | |
| 30-120 s | "stale" (prompts still accepted, warning shown). | |
| > 120 s / connector stopped / session killed | "offline": the API refuses new prompts (409 `runtime_offline`), the UI disables the composer, **metrics/overview polling stops** (one load, history kept), runtime list drops to 30 s. Auth, conversations, messages and telemetry history stay readable. Unfinished jobs are lease-expired and retried or failed by the existing logic. | back to ~$0 |
| New session | A new run is registered; the old run is marked aborted; the UI notices within <= 30 s and resumes 5 s polling. | |

Verified by tests (CPU-only, mock): `lib/session.test.ts` (polling intervals), `tests/connector/test_pacing.py`, `tests/api/test_jobs.py`; the offline banner / disabled composer are exercised by the mock Playwright suite. **Not verified on real AWS or Kaggle.**

## 3. DynamoDB capacity mode: on-demand (`PAY_PER_REQUEST`, already the template default)
For this usage on-demand costs **$0.02-$0.37/month**. Provisioned bills every hour of the month: 1 WCU + 1 RCU = **$0.57/month even when idle** (and too small: a streaming reply writes about 24 units/s), 25/25 = **$14.23/month at list** (free only if the always-free 25/25 allowance is confirmed for this account, and peak writes of ~24/s leave almost no headroom, so throttling is a real risk). Recommendation: on-demand, PITR off, TTL on, AWS-owned-key encryption (no KMS charge). Re-check only if usage grows beyond roughly 50 sessions x 2 h per month.
The DynamoDB parity template (`dynamodb-parity.yaml`) is a **throwaway PROVISIONED 25/25** table used for one real-service test and deleted immediately; it is capped at 25/25 by the governance test.

## 4. Function URL review (web and API)
- Both URLs are `AuthType NONE` (required for browsers and the Kaggle connector; IAM auth needs SigV4). Public reachability is therefore unavoidable; the controls are in the application: Cognito JWT / hashed runtime token verified **before any DynamoDB access** (malformed tokens fail in-process), 401 for everything else, request-body limits, per-key token buckets, idempotency keys, strict CORS origin list (CORS is not authentication).
- **Finding (read-only evidence, 2026-11-22):** this account's Lambda concurrency limit is **10**, with 10 unreserved. Lambda requires 10 to remain unreserved, so the previously drafted `ReservedConcurrentExecutions: 5` would have **failed the deployment**. Both templates now take a `ReservedConcurrency` parameter, default **0 (omitted)**, max 5; use it only after a Service Quotas increase. The account-wide limit of 10 is itself the parallelism bound.
- Per-instance rate limiting is not global (separate Lambda instances do not share memory). Junk traffic is billable: about **$0.48 per million unauthenticated requests** (Lambda duration + one log line; no DynamoDB access). WAF cannot attach to a Function URL, and AWS WAF/CloudFront are deliberately not in the plan (they add fixed monthly charges). Mitigation if abuse is ever observed: set the Function URL to disable/delete the URL (teardown script), or add `ReservedConcurrency` 1-2 after a quota increase.
- Open item to verify against current Lambda docs before the first deploy: recent guidance may require an additional `lambda:InvokeFunction` permission statement for public Function URLs; both templates currently grant only `lambda:InvokeFunctionUrl`.

## 5. Cost-surface guarantees (enforced by `tests/test_iac_governance.py`, `tests/test_no_aws_gpu.py`)
No template may contain: provisioned concurrency, WAF, CloudFront, CloudWatch alarms/insight rules/metric filters (custom metrics), canaries/X-Ray tracing, EC2/NAT/EIP, RDS, ELB, Secrets Manager, KMS keys, EventBridge rules/Scheduler, Step Functions, SQS, Kinesis, Route 53, or any SageMaker/Bedrock/ECS/EKS/Batch resource. Log groups are limited to 14 days (templates use 7). The application emits no CloudWatch custom metrics or EMF lines (grep of `apps/api` and `connector`). The optional CloudWatch **dashboard** in `resource-manifest.yaml` is the only metrics-related resource, is optional, and needs a free-slot check first.

## 6. Amplify (optional Path B) - important
**Disabling auto-build does not deactivate Amplify SSR hosting.** Auto-build only controls whether a Git push starts a (billable) build. A deployed branch keeps serving, and SSR requests/duration/data transfer keep billing per use. Stopping hosting means deleting the branch or the app (`teardown_order` lists `amplify_app_option`). The Next.js 16 warning stands: AWS documents managed SSR for Next.js 12-15 only (checked 2026-11-22; see `docs/AWS_AMPLIFY_NEXT16.md`); the custom deployment bundle passes only an offline layout check and is **not assumed supported until deployed and tested**. Path A needs no Amplify GitHub App and no `amplify:*` permissions.

## 7. Resources for the first deployment (all UNAPPROVED; see `infra/aws/resource-manifest.yaml`)
Cognito user pool (Lite) + app client · API Lambda (Python 3.13, x86_64, 512 MB, 15 s) + Function URL · web Lambda (Node 22, arm64, 1 GB, 20 s) + Lambda Web Adapter layer + Function URL · DynamoDB `enigma-dev` on-demand, TTL on, PITR off · 2 log groups (7 d) · S3 artifact bucket (zips only, public access blocked) · IAM roles (API: logs + table policy + ops-reader; web: logs only) · optional CloudWatch dashboard. Plus one throwaway parity table (deleted immediately).

## 8. Approvals still required before any AWS write
1. Region (proposal `us-east-1`).
2. Path A vs B (recommendation A).
3. Monthly ceiling (suggestion $5, notification budget $2).
4. Plan type and credit expiry read from the Billing console; free-tier applicability.
5. Per-resource written approval of section 7 and flipping `approval.approved` in `resource-manifest.yaml` in a reviewed commit.
6. Confirmation that the Lambda `lambda:InvokeFunction` permission question (section 4) is resolved.
7. Permission to run (a) the teardown dry run, (b) the real-DynamoDB parity run.
8. Root access-key deactivation; you launching Kaggle yourself.
