# BLOCKED: Vercel Hobby cannot host this private, organization-owned, commercial project

**Status of any Vercel deployment: UNVERIFIED. Nothing was deployed, imported, linked or attempted.** No Vercel account, CLI or token was used.

## Two independent blockers (checked against Vercel's own pages on 2026-11-21)

| # | Restriction | Source (exact wording) |
|---|---|---|
| 1 | Hobby is for personal / non-commercial use only | Vercel Terms of Service, section 4 "Hobby Plan" (page "Last Updated October 19, 2026"): *"You shall only use the Services under a Hobby plan for your personal or non-commercial use."* https://vercel.com/legal/terms |
| 2 | Hobby teams cannot deploy private organization repositories | Vercel Git docs, "Deploying private Git repositories" > "Using Hobby teams" (updated 2026-09-18): *"You cannot deploy to a Hobby team from a private repository in a GitHub organization, GitLab group, or Bitbucket workspace."* https://vercel.com/docs/git. Also https://vercel.com/docs/deployments/troubleshoot-project-collaboration: *"The Hobby Plan does not support collaboration for private repositories."* |

Enigma is closed-source commercial software and its canonical repository is the **private** organization repo `stokastisk-ai/enigma`. Both restrictions therefore apply.

Correction to earlier project notes: the Hobby plan page (`/docs/plans/hobby`) and the fair-use page cited in `skills.md` do **not** contain the personal-use wording; the Terms of Service do.

## What was deliberately NOT done
Making the repository public, transferring it to a personal account, mirroring it, creating a second account, using an unsanctioned CI/API path, or buying Pro. Each would either violate the owner's privacy requirement or a provider restriction, or spend money without approval.

## Compliant choices (owner decision required)
1. **Keep developing locally** (everything runs locally: API, web, connector, mock E2E, real Clerk dev instance). No deployment needed to continue. *Free, no restrictions.*
2. **Authorize a paid plan** (e.g. Vercel Pro) when you are ready for a commercial deployment. This needs your explicit approval; nothing will be purchased otherwise.
3. **A genuinely personal, non-commercial prototype** could be eligible for Hobby only if it is not this commercial project and its source lives in a personal (non-organization) repository. That would be a separate repo and your call; it is not prepared here and is not a workaround for the organization repo.
4. Evaluating other hosting providers is a separate decision with its own terms and costs; not assessed.

## Already prepared (so a later approved deployment is quick)
`apps/api/vercel.json` (daily prune cron), `.python-version` (3.13), `apps/api/index.py` (ASGI `app`), `.env.example` (names only), production-safety checks that refuse to boot with unsafe config, successful local production builds. Unverified until a real deployment exists: routing of the nested FastAPI project, function limits, preview env scoping.
