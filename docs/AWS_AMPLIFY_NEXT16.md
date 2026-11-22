# Amplify Hosting vs Next.js 16 — compatibility decision (2026-11-22)

**Status: `AMPLIFY_NEXT16_BLOCKED`.** Nothing deployed. No AWS resource exists (verified read-only, us-east-1: 0 Amplify apps, 0 CloudFormation stacks).

## Official evidence (fetched 2026-11-22)
| Claim | Source |
|---|---|
| Managed SSR ("Amplify Hosting compute") is documented for Next.js **12 through 15**; Next.js 16 is not mentioned. | https://docs.aws.amazon.com/amplify/latest/userguide/ssr-amplify-support.html |
| **Unsupported:** Next.js streaming, Edge middleware, on-demand ISR, `unstable_after`. | same page |
| "Amplify supports Next.js 15 applications without the need for an adapter." Other frameworks use adapters or the deployment specification. | https://docs.aws.amazon.com/amplify/latest/userguide/server-side-rendering-amplify.html |
| Runtimes: Node.js 20, 22, 24. Compute bundle <= 220 MB uncompressed, 15 min max, <= 25 routes, server on port 3000. | ssr-supported-features / ssr-deployment-specification pages |
| A web search found no AWS statement of Next.js 16 support (a third-party article claims it, unverified). | — |

## Decision
Managed Amplify SSR for Next.js 16.4.6 is **not documented as supported** → blocked. The *deployment specification* is a generic Node-server contract, so a custom bundle is feasible on paper:
`scripts/build_amplify_bundle.py` builds one from the standalone output; offline it passes all 14 spec checks (22.8 MB, no native binaries, SSR 200 on :3000, static fallback). **That is not proof of support**: Amplify's compute behaviour for Next.js 16 (`proxy.ts`, multiple `Set-Cookie`, redirects, buffering) can only be shown by a real, billed, approved deployment. No downgrade to Next 15 was made.

## App fit (audited)
- All 16 routes are dynamic (`ƒ`); no ISR/SSG, no `next/image`, no Edge runtime, no server actions.
- **Streaming is not used by Next**: the BFF (`app/api/enigma/[...path]/route.ts`) buffers upstream with `await upstream.text()`; the browser polls for token events. The documented "streaming unsupported" limit therefore does not affect today's design.
- `proxy.ts` (Next 16 middleware) runs on the Node runtime; "middleware" is documented only for 12–15 → unverified on Amplify.
- Auth: httpOnly cookies, same-origin CSRF check, Cognito JWT verification with cached JWKS (15 unit tests pass).
- Env: server code reads `process.env` at runtime; Amplify env vars are build-time → `amplify.yml` writes an explicit allowlist of public names to `.env.production` (see below).

## `infra/aws/amplify.yml` audit
Fixed: prefix-grep + `>>` append + `|| true` replaced by an explicit name allowlist with truncating write and multi-line refusal; `baseDirectory` `.amplify-hosting` for the spec path. Still unverified: `python3.13` in the Amplify build image, `appRoot`/relative `../../scripts` path behaviour in Amplify's monorepo runner.

## Alternatives (separately costed; none approved)
1. **Lambda Web Adapter + Function URL (+optional CloudFront)** — a distinct architecture, not "Amplify". Already prepared: `scripts/validate_nextjs_lambda.py` (0 failures locally; real adapter/CloudFront/cold start NOT RUN). Full response streaming possible via Function URL `RESPONSE_STREAM`, unneeded today.
2. **Amplify + custom deployment spec** (this PoC) — needs a real deployment to learn anything.
3. Downgrade to Next.js 15 — rejected by charter.

## Cost/approval
See `infra/aws/resource-manifest.yaml` (approval block all null) and `docs/AWS_COST_MATRIX.md`. Account plan, credit expiry and Free Tier eligibility cannot be read through the CLI and remain **owner-supplied/UNKNOWN**. Budgets are not caps. A push to GitHub triggers nothing in AWS today (no Amplify app, no webhooks, no Actions); connecting an Amplify branch later would make every push a billable build.
