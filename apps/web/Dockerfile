# Enigma web (Next.js 16 / React 19) on Railway. BUILD CONTEXT = REPOSITORY ROOT (Root Directory unset) because apps/web/lib/contracts.ts imports
# ../../../packages/contracts/src/api. Set service variable RAILWAY_DOCKERFILE_PATH=apps/web/Dockerfile.
FROM node:24-bookworm-slim AS build
WORKDIR /repo
ENV NEXT_TELEMETRY_DISABLED=1
COPY apps/web/package.json apps/web/package-lock.json apps/web/
RUN cd apps/web && npm ci --no-audit --no-fund
COPY apps/web apps/web
COPY packages/contracts packages/contracts
# Public identifiers only. Railway does NOT pass service variables as build args automatically; declare each one deliberately.
# Only needed if Clerk (not Cognito) is selected; the Cognito identifiers are read at RUNTIME and need no build arg.
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=""
ENV NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
# ENIGMA_STANDALONE=1: self-contained server.js (no sharp / native binaries; see next.config.ts).
RUN cd apps/web && ENIGMA_STANDALONE=1 npx next build

FROM node:24-bookworm-slim AS run
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=build --chown=10001:10001 /repo/apps/web/.next/standalone ./
COPY --from=build --chown=10001:10001 /repo/apps/web/.next/static ./.next/static
COPY --from=build --chown=10001:10001 /repo/apps/web/public ./public
EXPOSE 3000
USER 10001
# Default PORT=3000 (overridden by a Railway PORT variable; keep both equal so the domain target port is deterministic). Railway injects $PORT; server.js reads PORT and HOSTNAME. ENIGMA_DEV_AUTH is never set here, and the app refuses it on Railway.
CMD ["node", "server.js"]
