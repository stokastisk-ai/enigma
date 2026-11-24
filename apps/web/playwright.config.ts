import { defineConfig, devices } from "@playwright/test";
import { randomBytes } from "node:crypto";
import path from "node:path";

const root = path.resolve(__dirname, "../..");
const SECRET = randomBytes(32).toString("hex");          // throwaway per run; never a committed value
const API = "http://127.0.0.1:8787";
const WEB = "http://127.0.0.1:3100";

/**
 * Browser E2E against the REAL stack: production Next build + real FastAPI (in-memory store) + the labelled MOCK connector.
 * Auth uses the guarded dev login (no Clerk keys); that mode refuses to run on a hosted platform or in production.
 * System Google Chrome is used (channel: "chrome"), so no browser download is needed.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: WEB, channel: "chrome", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"], channel: "chrome" } }],
  webServer: [
    {
      command: `/usr/local/bin/python3.13 -m uvicorn enigma_api.main:create_app --factory --app-dir apps/api --host 127.0.0.1 --port 8787 --log-level warning`,
      cwd: root,
      url: `${API}/api/v1/health`,
      reuseExistingServer: false,
      env: { ENIGMA_ENV: "development", ENIGMA_DEV_AUTH: "true", ENIGMA_DEV_JWT_SECRET: SECRET, CRON_SECRET: "c".repeat(32), ENIGMA_RUNTIME_ONLINE_S: "4", ENIGMA_RUNTIME_STALE_S: "8", PYTHONDONTWRITEBYTECODE: "1", ENIGMA_FORBID_LOCAL_GPU: "1",
        // admin-only AWS page: SYNTHETIC demo data (mock mode never reads AWS), one admin id
        ENIGMA_ADMIN_SUBJECTS: "user-aws-admin", ENIGMA_AWS_OPS: "mock", ENIGMA_BUILD_SHA: "e2e0000" },
    },
    {
      command: "npx next start -p 3100 -H 127.0.0.1",
      cwd: __dirname,
      url: WEB,
      reuseExistingServer: false,
      // Clerk/Supabase variables are blanked so a local .env.local can never switch the mock suite into real-auth mode.
      env: { ENIGMA_API_URL: API, ENIGMA_DEV_AUTH: "1", ENIGMA_DEV_JWT_SECRET: SECRET, ENIGMA_DIST_DIR: ".next-e2e", NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "", CLERK_SECRET_KEY: "", NEXT_PUBLIC_SUPABASE_URL: "" },
    },
  ],
});
