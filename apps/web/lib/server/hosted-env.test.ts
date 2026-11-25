import assert from "node:assert/strict";
import { test } from "node:test";
import { devAuthEnabled, onHostedPlatform } from "./hosted-env.ts";

const SECRET = "x".repeat(32);

test("dev auth needs the opt-in flag and a 32+ character secret", () => {
  assert.ok(devAuthEnabled({ ENIGMA_DEV_AUTH: "1", ENIGMA_DEV_JWT_SECRET: SECRET }));
  assert.ok(!devAuthEnabled({ ENIGMA_DEV_JWT_SECRET: SECRET }));
  assert.ok(!devAuthEnabled({ ENIGMA_DEV_AUTH: "1", ENIGMA_DEV_JWT_SECRET: "short" }));
});

test("dev auth is refused on Railway even when everything else is set", () => {
  for (const name of ["RAILWAY_ENVIRONMENT_ID", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"]) {
    const env = { ENIGMA_DEV_AUTH: "1", ENIGMA_DEV_JWT_SECRET: SECRET, [name]: "production" };
    assert.ok(onHostedPlatform(env), name);
    assert.ok(!devAuthEnabled(env), name);
  }
});
