# CLAUDE CLI — ENIGMA AWS MIGRATION + SAFE PRIVATE GITHUB SYNC

Continue the **existing** Enigma project in `/media/waqasm86/External1/Waqas-Projects/Project-Enigma/`. Do not recreate or replace it. Treat `skills.md` **Section 15** as authoritative; earlier AWS and Vercel instructions are historical. Read entire `CLAUDE.md`, `skills.md`, `.claude/skills/enigma-aws-deploy/SKILL.md`, all AWS docs in `docs/`, `README.md`, relevant current source and tests, `.env.example`, `.gitignore`, `infra/aws`, and existing deployment scripts before changing anything.

## A. Immutable owner requirements

1. Enigma is private, proprietary and closed-source. Git remote MUST remain `https://github.com/stokastisk-ai/enigma.git`; do not change visibility, delete history, recreate repository, force push or publish private source. Normal reviewed necessary source commits and pushes to Enigma are explicitly authorized, after secret scan and test validation. Public repos `stokastisk-ai/stokastisk` and `stokastisk-ai/stokastisk-ai.github.io` are independent: read and analyze but do not push to them merely under this Enigma instruction.
2. **DO NOT USE LOCAL NVIDIA GEFORCE 940M, EVEN FOR PROBES OR TELEMETRY TESTS.** Do not execute `nvidia-smi`, initialize real `pynvml`, import/execute local GPU inference scripts with side effects, run `torch.cuda`, CUDA, vLLM, Stokastisk model servers, device-plugin tests or CUDA examples. Mock `pynvml` and every GPU access path for local unit/e2e tests. The only real GPU inference happens in an authorized user-launched Kaggle 2× NVIDIA T4 SM75 notebook. Never change laptop NVIDIA drivers.
3. Canonical **local** interpreter is `/usr/local/bin/python3.13`; verify using `readlink -f` and `sys.executable`. Use it for ALL local pip installations, pytest, server, helper and AWS-compatible packaging tasks: `/usr/local/bin/python3.13 -m pip ...`. Never `uv`, `ruff`, venv/virtualenv, Conda, Poetry, Pipenv, or Go. Do not use Python 3.14 just because AWS CLI embeds it. `aws` command is separate and allowed.
4. GPU observability dependencies ONLY `nvidia-ml-py` and `prometheus_client`, plus vLLM's native `/metrics`; no extra SDK/agents. Keep backend CPU-only and never package CUDA/vLLM into Lambda.
5. Do not use Vercel. Aim for AWS-native control plane, but **do not create billable AWS resources or risk charges without separately showing exact account-specific cost/eligibility and receiving explicit authorization**. A private Git push is allowed; AWS billing is not. Preserve all existing business data, files, migrations and source. No unreviewed mass deletions.

## B. Perform these phases in order

### Phase 0 — full read-only inventory

Run `pwd`, `git status --short`, `git remote -v`, `git branch --show-current`, `git log -5 --oneline`, `git ls-files | wc -l`, `git ls-files --others --exclude-standard`, `du -sh . .git apps/web/node_modules 2>/dev/null`; verify exact executable `/usr/local/bin/python3.13`. Record screenshot is ~788.9 MiB, while prior measurement was 718 MiB, and local dependency size is not GitHub payload. Review new `infra/aws/sql/`, Lambda template, `amplify.yml`, `apps/api/requirements-aws.txt`, Cognito auth and SQL tests. Use installed AWS CLI for read-only identity/plan interrogation; do not expose credentials/account ID. Look up repository visibility using authenticated GitHub; 404 can mean missing permission, not missing repo.

### Phase 1 — locally safe testing WITHOUT touching 940M

Inspect source and tests for `nvidia-smi`, `pynvml`, `nvmlInit`, `torch.cuda`, `cudaGetDeviceCount`, `stokastisk`, `ServerProcess`, `vllm`, GPU auto-detection and any import-time side effects. Convert any physical-GPU tests to deterministic mocks and mark remote-only tests. Only after confirming tests cannot touch the GPU, run `/usr/local/bin/python3.13 -m pytest`, local smoke script with mocked GPU, AWS SQL test runner, `npm run test:unit`, Next.js lint/typecheck/build and Chrome E2E. Record exact counts vs README claim of 167 Python passed / five live tests skipped / ten mock browser tests. If a test touches the laptop GPU, do NOT run it; fix isolation first. Pip install only specific trusted/pinned dependency packages with `/usr/local/bin/python3.13 -m pip`; do not install Nvidia/CUDA/vLLM/Stokastisk GPU binaries locally.

### Phase 2 — review AWS migration already in progress

Trace real Clerk/Supabase/Vercel dependencies in code (don't assume docs are updated), AWS Cognito auth provider, tenant boundaries, PostgreSQL schema and transaction semantics, migration scripts, job leases, event sequence numbers, reconnect, GPU telemetry ingestion and API contracts. Preserve working mock tests. Complete/test Python 3.13 FastAPI Lambda adapter and secure API entry, Cognito JWT verification with distinct tenants, Postgres store/atomic claims, and authenticated bounded event polling. Protect each workspace independently; plain RDS does not replicate Supabase RLS/auth functions. Do not change Stokastisk CUDA wheels or source. Keep Next.js 16 unless verified deliberate change is needed.

**Next.js hard gate:** Official AWS Amplify docs currently document managed Next.js SSR for versions 12–15, not confirmed 16. Recheck authoritative current docs; validate actual feature support (App Router, server actions, middleware, monorepo, auth cookies, Node runtime). Do not deploy an unsupported build, silently downgrade Next.js, or claim production compatibility from `npm run build` alone. If unsupported, document a minimal compatible, tested alternative as a proposal, with cost and privacy consequences, and mark AWS hosting BLOCKED.

### Phase 3 — AWS account cost matrix and dry-run

Use AWS CLI READ-ONLY with existing approved profile. Check plan Free/Paid, credits/balance/expiry, available Free Tier allowances, region and service-specific spend risk. Historical account notes suggested Paid plan with credits, so do not assume Free. Validate IAM/read-only capabilities. Evaluate Amplify SSR, Cognito, Lambda, Lambda Function URL vs HTTP API, RDS/DB networking/VPC/NAT, DynamoDB alternative, S3/CloudFront, logs, Build minutes, egress, Route53, IPv4, secrets and certificate costs. Save sourced `docs/AWS_COST_MATRIX.md` with quote/forecast, status and exact blocking dependency. Design IaC in `infra/aws/` and validate offline/dry-run. **Never execute CloudFormation deploy, SAM deploy, `aws amplify create-app`, `aws rds create-*`, `aws iam create-*`, or any other mutating AWS API in this run without separately obtained deployment-cost approval.** Budget alarm is not a hard spending cap.

### Phase 4 — secure Git changes and private push

Inventory remote tracked files and local changes. Determine which files really belong in Enigma source: `apps/web`, `apps/api`, `connector`, `packages/contracts`, `tests`, `infra/aws`, needed legacy migrations, docs, `.claude` skill, `skills.md`, `CLAUDE.md`, `.env.example`, `.gitignore`, package lock, requirements and small sample fixtures. Avoid committing `node_modules`, `.next*`, `.pytest_cache`, Playwright output, `.env.local`, AWS keys, notebook secrets, wheels, model weights, large archives or temporary logs. Review every proposed deletion; do not clear remote repo wholesale. Perform tracked and historical secret scans without printing values. Run `git diff --check`, relevant tests and audit changed file list. Stage explicit safe paths only, commit with clear message, push normal fast-forward to verified private Enigma remote; if permissions unavailable, report BLOCKED and commands rather than fake success. Verify remote SHA and private visibility. Never push to public SDK/docs repos as side-effect.

### Phase 5 — gated AWS deployment only if individually approved

If and only if owner explicitly approved enumerated resource costs and verified free/credit eligibility, deploy narrowly to approved region with tags, least privilege, no public database, no unnecessary NAT/ALB/EC2/Route53, and known teardown steps. Verify real deployed frontend/API/Cognito/DB and multi-tenant authorization. Do not present a code-only migration as hosted. If no approved safe cost profile, **stop provisioning while still completing local code/testing/private Git push**.

### Phase 6 — REMOTE Kaggle acceptance (no local GPU)

Prepare/update notebook for Stokastisk SDK 0.1.0 build 5, 2 × T4 SM75, vLLM 0.25.1 / Triton, Qwen2.5-0.5B TP=1, actual `/health`, `/metrics`, real generated chunks, per-GPU nvidia-ml-py, ordered event relay, offline/reconnect. The user runs Kaggle notebook in authorized session. Never run on Ubuntu laptop. Report Kaggle as NOT RUN unless real evidence is supplied. Cloud-environment chat test only after authorized HTTPS API is deployed.

### Phase 7 — final audit and factual reporting

Update `README.md`, `skills.md` Section 15 only if lessons demand, AWS docs, test report, `.env.example` variable NAMES ONLY, Git push report and blocked setup notes. Report each item as DONE, BLOCKED or NOT RUN: interpreter, local GPU isolation, local Python/browser/SQL tests, AWS account plan/costs, Amplify Next16 compatibility, Cognito, Lambda, database, private GitHub push/commit, AWS live hosting, Kaggle 2×T4 build 5 and browser→AWS→Kaggle chat+telemetry. Provide actual local size and safe cleanup log. Do not claim $0 guarantee or real deployment unless proven.

**Execute maximum permitted local work and private-source Git sync now. Do not ask me to repeat supplied context. Do not use my old NVIDIA GPU. Do not provision chargeable AWS services without separate explicit approval.**
