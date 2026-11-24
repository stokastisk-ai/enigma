# Enigma web (Next.js 16 / React 19)

Authenticated console + BFF. The browser only talks to this app: `/api/enigma/*` verifies the session (Cognito cookies, Clerk, or the guarded local dev login), allow-lists method and path, validates bodies with zod and forwards to the FastAPI service with a server-side bearer token. Auth routes live under `/api/auth/*`.

```bash
npm ci
npm run dev            # needs ENIGMA_API_URL and an auth provider; see ../../.env.example
npm run typecheck && npm run lint && npm run test:unit
npm run test:e2e       # builds into .next-e2e and runs the mock-mode Playwright suite (guarded dev login, no provider keys)
```

Production build: `apps/web/Dockerfile` (standalone output, built from the repository root because `lib/contracts.ts` imports `packages/contracts`). Deployment and variables: [../../docs/DEPLOYMENT.md](../../docs/DEPLOYMENT.md).
