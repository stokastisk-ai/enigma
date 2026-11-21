# AWS deployment — NOT DEPLOYED. Approval gate not passed.

Account (read-only check 2026-11-21): **PAID plan, ACTIVE, USD 99.99 credits, expiry unknown, no resources.** See `AWS_COST_MATRIX.md`. Do not create anything until the owner approves the items there.

## Proposed one-at-a-time sequence (after approval; stop on any unexpected charge)
Tags on everything: `Project=Enigma`, `Environment=dev`, `Owner=<owner>`. Use a non-root role, one approved region, always pass `--profile` and `--region`.
1. Cognito user pool + **public** app client (no secret; `ALLOW_USER_PASSWORD_AUTH`, `ALLOW_REFRESH_TOKEN_AUTH`; email sign-up). Real two-user test of sign-up/confirm/sign-in/sign-out and the API's claim checks.
2. Database (Track A: RDS PostgreSQL db.t4g.micro, private only, Single-AZ, 20 GB, backups minimal) **or** Track B. Apply `infra/aws/sql/*.sql` to a **dedicated** database; create a login role `IN ROLE enigma_app`; never the `agentic_workspace` DB. Resolve the NAT/JWKS question first.
3. Lambda API from `infra/aws/template.yaml` (draft; `aws cloudformation validate-template` passes, nothing created). Package: `python3.13 -m pip install -r apps/api/requirements-aws.txt -t build/` for the Lambda platform, then zip `build/` + `apps/api/enigma_api` + `lambda_handler.py`.
4. Amplify Hosting from `infra/aws/amplify.yml` **only after** a Next.js 16 build is proven on Amplify.
5. Real end-to-end: browser → Cognito → API → Kaggle connector (GPU0/GPU1 NVML + vLLM `/metrics`) → chunks → history after disconnect.

## Teardown (record before provisioning)
Delete in reverse order: Amplify app → Lambda stack → RDS (take or skip final snapshot deliberately) → Cognito pool → log groups → SSM parameters; then confirm in Billing/Cost Explorer that charges stop. Budget alerts (if approved) are notifications, not caps.

## Local run (unchanged, zero spend)
`python3.13 -m uvicorn index:app --app-dir apps/api --port 8787` · `cd apps/web && npm run build && npx next start -p 3100`. Postgres/Cognito modes need the env names in `.env.example`.
