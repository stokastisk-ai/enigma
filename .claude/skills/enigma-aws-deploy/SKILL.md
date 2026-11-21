---
name: enigma-aws-deploy
description: Audit and migrate the existing private Enigma Next.js/FastAPI observability SaaS to AWS without local GPU use or unapproved AWS charges, while preserving Python 3.13 and private Git history.
---

# Enigma AWS deployment skill

Read `/media/waqasm86/External1/Waqas-Projects/Project-Enigma/skills.md` Section 15 in full, plus `CLAUDE.md`, `CLAUDE_CLI_AWS_FINAL_PROMPT.md`, current source, docs, tests, Git changes, and AWS cost matrix.

**Hard stop:** Never initialize or probe the local 940M. Mock local `pynvml`/CUDA for all tests; remote Kaggle 2xT4 only for real inference. Use `/usr/local/bin/python3.13` with `-m pip`/pytest; no uv/ruff/venv/Go. AWS resource writes only after account-specific eligibility check and charge-risk approval. Normal reviewed private Git source pushes are authorized after a secret check; no forced/destructive push. `stokastisk-ai/stokastisk` and docs repos are separate and not automatically writable.

Workflow: (1) inventory current implementation and remote identity; (2) audit local GPU calls before tests; (3) run safe CPU/mock validation; (4) verify Amplify Next.js 16 support or block; (5) implement AWS Cognito/Lambda/database/event adapters with tenant isolation; (6) price and validate IaC without provisioning; (7) review diff and push needed source only to private Enigma; (8) deploy only if resource-cost authorization exists; (9) separately report Kaggle build-5 acceptance as not run until truly tested; (10) safely clean only known generated artifacts and document results.
