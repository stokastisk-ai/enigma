# Enigma — persistent Claude Code instructions

Read `skills.md` IN FULL before any operation. **Section 17 is the highest-precedence AWS and security charter**, overriding older AWS Sections 14–16 and legacy Vercel/Clerk/Supabase requirements. Read `.claude/skills/enigma-aws-deploy/SKILL.md` and the current `CLAUDE_CLI_AWS_DASHBOARD_PROMPT.md` for the next task.

Keep Enigma closed-source in private `stokastisk-ai/enigma`. Use ONLY `/usr/local/bin/python3.13` for local Python; `-m pip` for installs. Never uv, ruff, venv, Poetry, Conda or Go. NEVER probe local NVIDIA GeForce 940M; on Kaggle only, user-launched 2× Tesla T4 runs Stokastisk. AWS is a CPU control plane, no AWS GPU/Bedrock/SageMaker or hosted inference. GPU observability instruments: nvidia-ml-py, prometheus_client, built-in vLLM metrics only (Kaggle). CloudWatch is for AWS platform operations only.

No root credential usage for deployment. Never request root password or keys. Do not create any AWS resource, CloudWatch dashboard, DynamoDB table or change IAM without safe nonroot identity, current free-tier/credit check and explicit owner-approved resource manifest and spending risk. You MAY modify/test local code and push reviewed secret-free normal commits to the private Enigma repo. Never force push or delete unknown user files. Never claim cloud or real T4 success from mocks.
