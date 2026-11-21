---
name: enigma-aws-deploy
description: Maintain and safely deploy proprietary Enigma AWS control plane and create owner-only AWS operations dashboards. Only Kaggle dual T4 may run Stokastisk inference.
---

# Enigma AWS deployment and operations dashboard skill

1. Read root `CLAUDE.md`, all of `skills.md` (especially authoritative Section 17), `CLAUDE_CLI_AWS_DASHBOARD_PROMPT.md`, current source/IaC/tests and cost/deployment reports.
2. Review Git private remote, Python interpreter, AWS identity and no-local-GPU guard. Do not use root credentials for deployment. No AWS writes without separately approved exact manifest, region and ceiling.
3. Build/test Enigma-admin-only AWS inventory page using FastAPI+boto3 read-only design, existing shadcn/Recharts; add a single <=50 metric CloudWatch dashboard template, not a deployed dashboard without approval.
4. Finish AWS DynamoDB parity, Next.js Lambda Web Adapter, Cognito/JWKS, secure CloudFront routing, bounded polling; preserve existing PostgreSQL code and other tests.
5. AWS hosts no GPU inference or hosted LLM. Kaggle 2xT4 exclusively for explicitly user-launched Stokastisk; never local 940M or AWS GPU. GPU metrics only native vLLM, nvidia-ml-py, prometheus_client.
6. Run CPU-only Python3.13 tests, browser tests, secret scans. Push reviewed source to the existing private Enigma repo; no force push or destructive cleanup. Report true deployment and billing statuses.
