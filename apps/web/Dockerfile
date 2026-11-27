# Enigma web (Next.js 16 / React 19). BUILD CONTEXT = REPOSITORY ROOT because apps/web/lib/contracts.ts imports ../../../packages/contracts/src/api.
# Multi-arch base (linux/arm64 on the OCI A1). Runtime dependencies are pure JavaScript (better-auth, pg, nodemailer): no native addons.
FROM node:24-bookworm-slim AS build
WORKDIR /repo
ENV NEXT_TELEMETRY_DISABLED=1
COPY apps/web/package.json apps/web/package-lock.json apps/web/
RUN cd apps/web && npm ci --no-audit --no-fund
COPY apps/web apps/web
COPY packages/contracts packages/contracts
# The app currently ships no static assets, and Git does not track an empty apps/web/public: create it so the runtime stage's COPY works either way.
RUN mkdir -p apps/web/public
# No NEXT_PUBLIC_* build arguments are needed: auth settings are read at RUNTIME (server only).
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
# server.js reads PORT and HOSTNAME. ENIGMA_DEV_AUTH is never set in a deployment.
CMD ["node", "server.js"]
