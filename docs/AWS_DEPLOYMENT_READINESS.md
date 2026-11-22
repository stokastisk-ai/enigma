# Enigma AWS deployment readiness report (2026-11-22)

**Verdict: NOT READY — `AMPLIFY_NEXT16_BLOCKED`; Lambda-Web-Adapter path blocked on missing IaC and owner approvals.** AWS resources created: **NONE** (read-only: 0 Amplify apps, 0 stacks in us-east-1). No live Cognito/DynamoDB/Lambda/CloudFront/Amplify, Kaggle 2×T4 or browser→AWS→Kaggle result exists.

## 1. Gates
| Gate | Status |
|---|---|
| Non-root identity (`enigma-deployer`, assumed-role, explicit `--profile/--region`) | VERIFIED today; root-key removal PENDING (owner) |
| Next.js 16 managed Amplify support | NOT DOCUMENTED (12–15 only); custom bundle locally conformant, **not proof** |
| Web-tier IaC (LWA path) | DRAFTED offline (`infra/aws/web-template.yaml`), governance-tested, NOT deployed |
| Region | `us-east-1` PROPOSED, not approved |
| Plan / credits / expiry / Free Tier | UNKNOWN (owner reads Billing → Credits); budgets are not caps |
| Dollar ceiling, per-resource written approval | NOT GIVEN |
| Auto-deploy triggers on push | None in AWS; Vercel GitHub App (all org repos, push events) exists but `2c8d202` produced 0 statuses/checks/deployments — owner to confirm no Vercel project is linked |
| Deployer policy | covers lambda/dynamodb/cognito/logs/cloudfront/s3/cloudformation; **no `amplify:*`**; GPU/Bedrock/NAT/ELB/Route53 explicitly denied |

## 2. Proposed resource inventory (all UNAPPROVED; tag `Project=Enigma,Environment=dev,Owner=<owner>`)
Preferred first live test = **Path A**, Track B (DynamoDB). Region `us-east-1`.
| # | Resource | Config | Notes |
|---|---|---|---|
| 1 | Cognito user pool + app client | Lite tier, no hosted UI, no secret in browser | public ids only in env |
| 2 | DynamoDB table `enigma-dev` | on-demand or 25/25 provisioned, TTL attr, PITR off | after throwaway `enigma-parity-*` real-parity table (<$0.01) |
| 3 | Lambda `enigma-api` | python3.13, x86_64, 512 MB, 15 s, no reserved concurrency (account limit 10) | no GPU libs |
| 4 | Lambda Function URL (API) | auth NONE + app JWT | public at edge |
| 5 | Lambda `enigma-web` | nodejs22.x/24.x, ARM64, 1 GB, 20 s, no reserved concurrency, LWA layer | IaC: `infra/aws/web-template.yaml` (draft) |
| 6 | Lambda Function URL (web) / CloudFront (optional) | OAC or secret origin header if CloudFront | decision pending |
| 7 | CloudWatch log groups ×2 | retention 7 days | no custom metrics |
| 8 | S3 bucket | Lambda zips only, block public access, SSE | lifecycle expiry |
| 9 | IAM roles ×2 | api: logs+table+ops-reader; web: logs only | |
| 10 | CloudWatch dashboard (optional) | 30 AWS-native metrics | only if free slot (`check_dashboard_quota.py`) |
Alternative (Path B): `AWS::Amplify::App`+branch replaces 5–6; **requires** widening deployer policy, installing the Amplify GitHub App on the private repo, and makes every push billable.
Never created: EC2/GPU, SageMaker, Bedrock, NAT, ALB, Route53, Secrets Manager, RDS.

## 3. Charge exposure (LIST prices, us-east-1, `scripts/aws_cost_model.py`; model estimates, not quotes)
| Workload | Path A total | Path B total | Notes |
|---|---:|---:|---|
| low (10 Kaggle h, 500 views, 2 MAU) | $0.37/mo | $1.26/mo | Free tiers would lower A to ≈$0; applicability UNKNOWN |
| moderate | $2.40 | $3.87 | |
| high (130 h, 30k views, 25 MAU) | $7.44 | $10.80 | |
Unbounded risks: public Function URLs / Amplify endpoints can be hit by anyone (billable requests); the account-wide Lambda limit (10) bounds Lambda only (reserved concurrency cannot be used on this account); no WAF planned ($15/mo+ if added); log ingestion; forgotten parity table; Amplify build minutes. Credits ($99.99 reported, expiry unknown) are not a cap. **Suggested ceiling to approve: $10/month for Path A**, with a console-set Budget alert at $5 (notification only).

## 4. Teardown (reverse order; verify each with read-only list calls)
CloudWatch dashboard → CloudFront (disable, wait, delete) → web Function URL → `enigma-web` → API Function URL → `enigma-api` → parity/real DynamoDB tables → log groups → Cognito pool → empty+delete S3 bucket → IAM roles. Path B first: delete Amplify branch/app, then remove the Amplify GitHub App grant. Parity runs auto-delete their stack; verify in console. `scripts/aws_teardown.py` exists (dry-run by default, unit-tested offline; never executed against AWS).

## 5. Approvals needed from the owner (explicit, written, per operation)
1. Region (`us-east-1` or other). 2. Path A or B (A recommended). 3. Monthly dollar ceiling. 4. Credit expiry date + plan type from Billing console. 5. Permission to write web-tier IaC and teardown script (offline, no AWS). 6. Then, per resource group, the exact AWS writes listed in §2. 7. For Path B only: widen deployer policy and install the Amplify GitHub App. 8. Confirm no Vercel project is linked to `stokastisk-ai/enigma`. 9. Root-key deactivation (owner, console, after non-root path proven). 10. Launch Kaggle 2×T4 build-5 validation yourself.

## 6. Next offline steps (no approval needed)
Draft web-tier SAM/CloudFormation (LWA layer, URL, optional CloudFront+OAC) with `cfn-lint`-style local validation; write+test teardown script; Node fallback assembler for Path B.
