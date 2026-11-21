# Enigma — Permanent Claude Code project policy

Read `skills.md` **completely** at the beginning of every session. **Section 16 is the newest, highest-precedence GPU compute and AWS policy**, overriding conflicting older instructions. Also read `.claude/skills/enigma-aws-deploy/SKILL.md` and `CLAUDE_CLI_KAGGLE_ONLY_PROMPT.md`.

**GPU hard rule:** ONLY the user's explicitly launched Kaggle 2× NVIDIA Tesla T4 (SM75) notebook may execute Stokastisk/vLLM inference. NEVER use, create or propose AWS EC2 NVIDIA GPU, SageMaker GPU, AWS accelerator resources or any AWS-hosted inference fallback. NEVER probe/use the laptop's GeForce 940M, including NVML/nvidia-smi/CUDA diagnostics. Local GPU tests use synthetic mocks. AWS is the control plane only.

Use `/usr/local/bin/python3.13` and `-m pip` exclusively for local Python; no uv, ruff, venv, Poetry, Conda or Go. Keep Enigma proprietary and `stokastisk-ai/enigma` PRIVATE. Reviewed, secret-free non-destructive Git pushes of Enigma are authorized; force pushes and other repo changes are not. AWS account reportedly has root access keys: do not deploy using root; create a safe remediation plan, not unattended credential changes. AWS PAID plan/credits are not $0 guarantees. No chargeable provisioning absent explicit itemized approval. No credential values in Git or docs.
