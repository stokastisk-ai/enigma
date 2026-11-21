# Enigma — Claude Code permanent project instructions

Read `skills.md` **in full** at the start of every session, especially Section 12 (2026-11-21 revision) controlling secret management, private GitHub, Hobby eligibility, zero-spend deployment, and safe cleanup. Keep Enigma proprietary and `stokastisk-ai/enigma` PRIVATE; never commit credentials. Treat any keys pasted into chat as exposed and require rotation prior to live deployment. Never make the repository public to satisfy Vercel Hobby.

Work inside `/media/waqasm86/External1/Waqas-Projects/Project-Enigma/`. Preserve the existing implementation and test suite. Use manually installed `python3.13`; never uv, ruff, venv, conda, or Go. GPU metrics: only `nvidia-ml-py`, `prometheus_client`, and native vLLM metrics. Read `.claude/skills/enigma-build/SKILL.md` when relevant. Do not claim external provider or Kaggle tests have passed without execution.
