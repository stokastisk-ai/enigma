import { defineConfig, devices } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * LIVE browser test against a real Clerk DEVELOPMENT instance (keys come from the ignored apps/web/.env.local; nothing is printed).
 * The API runs with the in-memory store (Supabase is not part of this test) and verifies Clerk tokens through the real JWKS.
 * Run:  ENIGMA_LIVE_CLERK=1 npx playwright test -c playwright.live.config.ts
 */
const root = path.resolve(__dirname, "../..");
const env: Record<string, string> = {};
try {
  for (const line of fs.readFileSync(path.join(__dirname, ".env.local"), "utf8").split("\n")) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (m) env[m[1]] = m[2].replace(/^['"]|['"]$/g, "");
  }
} catch { /* no env file: the spec skips itself */ }

const WEB = "http://localhost:3100";
export default defineConfig({
  testDir: "./e2e-live",
  timeout: 240_000,
  expect: { timeout: 20_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: WEB, channel: "chrome", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "live", use: { ...devices["Desktop Chrome"], channel: "chrome" } }],
  webServer: [
    {
      command: "/usr/local/bin/python3.13 -m uvicorn index:app --app-dir apps/api --host 127.0.0.1 --port 8787 --log-level warning",
      cwd: root,
      url: "http://127.0.0.1:8787/api/v1/health",
      reuseExistingServer: false,
      env: { ENIGMA_ENV: "development", ENIGMA_STORE: "memory", CLERK_ISSUER: env.CLERK_ISSUER ?? "", CLERK_JWKS_URL: env.CLERK_JWKS_URL ?? "", CLERK_AUTHORIZED_PARTIES: env.CLERK_AUTHORIZED_PARTIES ?? "", PYTHONDONTWRITEBYTECODE: "1" },
    },
    { command: "npx next start -p 3100 -H localhost", cwd: __dirname, url: WEB, reuseExistingServer: false },   // loads .env.local itself
  ],
});
