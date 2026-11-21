---
name: enigma-aws-deploy
description: Continue the existing proprietary Enigma AWS control-plane migration while keeping all GPU inference exclusively on Kaggle 2x Tesla T4, protecting private Git, Python 3.13 rules, and AWS cost gates.
---

# Enigma — AWS control plane, Kaggle GPUs only

Read root `CLAUDE.md`, the entire `skills.md` (**Section 16 highest precedence**), `CLAUDE_CLI_KAGGLE_ONLY_PROMPT.md`, existing AWS cost/security/architecture/deployment documents, and relevant source. Do not rebuild the application.

Absolute prohibitions: **NO AWS GPU** of any kind (EC2 G/P/DL, SageMaker, accelerator instances, GPU container nodes, Bedrock inference fallback); **NO local GeForce 940M access or NVML initialization**. Kaggle's authorized 2x Tesla T4 only runs Stokastisk/vLLM. AWS Lambda/CloudFront/Cognito/database only host the web/control plane, and are still subject to billable-resource approval.

Use `/usr/local/bin/python3.13` / `-m pip`; no uv, ruff, venv or Go. Mock local GPU metrics. Audit root AWS keys/security; do not execute deployment as root or silently rotate credentials. Do not create billable resources until region, credits/expiry, pricing and explicit user approval are established. Keep `stokastisk-ai/enigma` private; reviewed secret-free normal pushes permitted, no force/reset. Prior reported test count 184 pass/6 skip is historical; rerun relevant tests. Report actual findings and blockers.
