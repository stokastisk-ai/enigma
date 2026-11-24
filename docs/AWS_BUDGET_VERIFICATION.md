# AWS budget and billing verification (2026-11-22)

Read-only evidence from the non-root IAM Identity Center profile `enigma-deployer`, region `us-east-1`. No AWS write was executed. Account IDs, ARNs and e-mail addresses are deliberately not recorded here.

## Budgets (confirmed by AWS API)
| Budget | Limit | Period | Type | Recurring | Alerts |
|---|---|---|---|---|---|
| `Enigma-Monthly-USD20` | 20 USD | MONTHLY | COST | yes (open-ended end date) | 1: ACTUAL > 25 % (= $5), 1 e-mail subscriber |
| `My Zero-Spend Budget` | 1 USD | MONTHLY | COST | yes | 1: ACTUAL > $0.01 absolute, 2 e-mail subscribers |

- No budget actions are configured on the account. Budget health: HEALTHY. Spend so far this month on both: 0.0 actual, 0.0 forecast.
- **Finding (important): both budgets have `IncludeCredit: true`.** They measure spend *net of credits*. While promotional credits absorb charges, neither the $5 alert nor the $0.01 zero-spend alert will fire. Section 20 asked for gross (pre-credit) monitoring. Fix is a console edit by the owner (Cost types: untick *Credits*); this task did not modify budgets.
- Only one alert exists on the $20 budget. Recommended additions (owner, console): ACTUAL at $10 (50 %), $15 (75 %), $20 (100 %), plus an optional FORECASTED 100 %. Status: **NOT CONFIGURED**.
- E-mail subscription status is not exposed by the Budgets API; the audit reports subscriber presence only.
- **AWS Budgets are asynchronous notifications, not spending limits.** Cost data lags by roughly 8-12 hours or more, so spend can overshoot a threshold before any alert arrives. They do not stop or throttle any resource.

## Credits, plan and Free Tier
| Item | Result |
|---|---|
| Account plan | `freetier:GetAccountPlanState` → `PAID`, `ACTIVE` (API) |
| Plan-reported remaining credits | `0.0 USD` (API). **Conflicts with the ~$99 the owner observed; the API may count only plan-sign-up credits.** Treat the balance as UNVERIFIED |
| Cost Explorer this month (1-22 Nov) | gross (UnblendedCost) ≈ $0.00004, net (NetUnblendedCost) = $0; a credit line of the same size was applied, so some credit is active |
| Credit balance and expiry | **UNVERIFIED — AWS BILLING CONSOLE REQUIRED.** `billing:GetCredits` → AccessDenied "IAM user access not activated". Only the account owner can enable IAM billing access; do not use root keys |
| Eligible services / restrictions | **UNVERIFIED — AWS BILLING CONSOLE REQUIRED** |
| Free Tier applicability | `freetier:GetFreeTierUsage` works and lists Always-Free items already in use (Glue, SQS, SNS, KMS...). Lambda/DynamoDB/Cognito applicability for a PAID-plan account stays UNVERIFIED |

No expiry date is estimated or derived.

## Cost projections (Kaggle-only, list price vs documented free allowances; `scripts/aws_session_cost.py (removed 2026-11-24)`)
List price is the model estimate; "with free tiers" assumes always-free allowances apply (unverified here). Credits and actual billed charges are separate: actual billed charge to date is $0.00.
| Scenario | LIST | Free tiers |
|---|---:|---:|
| 1 x 1 h | $0.04 | $0.02 |
| 1 x 2 h | $0.08 | $0.04 |
| 10 x 1 h | $0.35 | $0.19 |
| 10 x 2 h | $0.68 | $0.37 |
| Idle month | $0.01 | $0.00 |
Unmodelled: bot traffic against the public Function URLs (~$0.48 per million junk requests), cold starts, other users. The $20 budget is **not** authorisation to spend $20; the deployment ceiling remains unapproved.

## Tooling
`scripts/enigma_aws_budget_audit.py` exit codes: 0 verified, 2 refused, 10 missing, 11 misconfigured (or open warning under `--strict`), 12 permission denied, 13 unverified. Tests: `tests/test_budget_audit.py` (synthetic CLI responses).
Latest real run: VERIFIED with 5 warnings (credit-inclusive metric, $10/$15/$20 alerts absent, no forecast alert).
