# Enigma — permanent Claude Code instructions (AWS migration)

Always read the full root `skills.md`, **especially Section 15 (LATEST, overrides Sections 1–14)**, then `.claude/skills/enigma-aws-deploy/SKILL.md` before any work.

- Proprietary Enigma lives at `/media/waqasm86/External1/Waqas-Projects/Project-Enigma/`; push reviewed source only to PRIVATE `stokastisk-ai/enigma` with secret auditing and no force push or history deletion. Public Stokastisk SDK/docs repos are read-only unless separately authorized.
- **NEVER use, initialize, probe or test this laptop's NVIDIA GeForce 940M GPU**. No local `nvidia-smi`, NVML init, CUDA, PyTorch CUDA, vLLM or Stokastisk inference. Mock all local GPU telemetry. Real GPU acceptance only on user-launched authorized Kaggle dual-T4 notebook or separately approved remote host.
- All local Python and pip commands must use `/usr/local/bin/python3.13`; AWS CLI's embedded Python runtime is independent. Do not use `uv`, `ruff`, venv, virtualenv, Poetry, Conda, Go or unapproved Python alternatives.
- AWS only for new hosting; preserve current Next.js 16, FastAPI, connectors, tests and legacy provider code until AWS equivalence passes. AWS Amplify's official Next.js SSR guide documents 12–15; verify actual Next.js 16 support before any deployment.
- No AWS provisioning without verified region-specific plan/credits/quota/combined pricing **and explicit approval for any charge risk**; no claims of $0 indefinitely. No credentials in Git/Markdown/logs. No local GPU work.
- Read `CLAUDE_CLI_AWS_FINAL_PROMPT.md` for current assignment. Prior AWS and Vercel prompts are historical. Preserve untracked user files; no blanket deletion. Report actual tests, Git push, AWS deployment and Kaggle validation separately.
