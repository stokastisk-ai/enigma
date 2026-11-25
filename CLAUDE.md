# Enigma — Claude Code persistent entrypoint (2026-11-22)

Before any modification or deployment, READ `skills.md` **in full**; Section 19 is the authoritative latest deployment and security charter, superseding inconsistent earlier sections 1–18. Then read `.claude/skills/enigma-aws-deploy/SKILL.md` if present and `CLAUDE_CLI_AMPLIFY_NEXT16_DEPLOY_PROMPT.md`.

Enigma is proprietary; keep `stokastisk-ai/enigma` PRIVATE. Never access the local 940M; user-launched Kaggle 2×T4 only for inference. AWS is CPU-only control plane. Use `/usr/local/bin/python3.13`, no uv/ruff/venv/Go. AWS profile `enigma-deployer` is a recently verified nonroot SSO identity but **not AWS cost authorization**. Official Amplify managed Next.js SSR docs currently cover versions 12–15, so Next.js 16 compatibility is BLOCKED pending proof. No AWS resource writes, auto-build triggering Git push, or billed deployment absent separately reviewed resource/cost approval. Preserve user files and historical Git.
