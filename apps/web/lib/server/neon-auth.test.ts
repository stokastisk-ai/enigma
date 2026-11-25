import assert from "node:assert/strict";
import { test } from "node:test";
import { cacheClearForTests, cacheGet, cachePut, hasNeonCookie, neonAuthConfig, neonRequireVerified, sessionKey } from "./neon-auth.ts";

const OK = { ENIGMA_NEON_AUTH: "on", NEON_AUTH_BASE_URL: "https://ep-x.neonauth.us-east-2.aws.neon.tech/neondb/auth/", NEON_AUTH_COOKIE_SECRET: "s".repeat(32) };
const jwt = (expSec: number) => `h.${Buffer.from(JSON.stringify({ exp: expSec })).toString("base64url")}.s`;

test("Neon auth is off unless explicitly enabled and fully configured", () => {
  assert.deepEqual(neonAuthConfig(OK), { baseUrl: "https://ep-x.neonauth.us-east-2.aws.neon.tech/neondb/auth", secret: "s".repeat(32) });
  assert.equal(neonAuthConfig({ ...OK, ENIGMA_NEON_AUTH: undefined }), null);
  assert.equal(neonAuthConfig({ ...OK, ENIGMA_NEON_AUTH: "true" }), null);
  assert.equal(neonAuthConfig({ ...OK, NEON_AUTH_COOKIE_SECRET: "short" }), null);
  assert.equal(neonAuthConfig({ ...OK, NEON_AUTH_BASE_URL: "http://ep-x.example/neondb/auth" }), null); // no cleartext Auth URL
  assert.equal(neonAuthConfig({ ...OK, NEON_AUTH_BASE_URL: "https://user:pw@ep-x.example/auth" }), null);
  assert.equal(neonAuthConfig({ ...OK, NEON_AUTH_BASE_URL: "not a url" }), null);
});

test("verified e-mail is required unless explicitly relaxed", () => {
  assert.equal(neonRequireVerified({}), true);
  assert.equal(neonRequireVerified({ ENIGMA_NEON_REQUIRE_VERIFIED_EMAIL: "false" }), false);
  assert.equal(neonRequireVerified({ ENIGMA_NEON_REQUIRE_VERIFIED_EMAIL: "0" }), true);
});

test("only the SDK's own cookie names count as a Neon cookie (presence, not trust)", () => {
  assert.equal(hasNeonCookie(["__Secure-neon-auth.session_token"]), true);
  assert.equal(hasNeonCookie(["neon-auth.session_data"]), true);
  assert.equal(hasNeonCookie(["enigma_access", "neon-authx", "my-neon-auth.session_token", "session_token"]), false);
  assert.equal(hasNeonCookie([]), false);
});

test("minted tokens are cached briefly, never past their own expiry, and keyed by a hash", () => {
  cacheClearForTests();
  const now = 1_000_000_000_000;
  const k = sessionKey("user-1:cookie-value");
  assert.match(k, /^[0-9a-f]{64}$/);
  assert.ok(!k.includes("cookie-value"));
  const longLived = jwt(Math.floor(now / 1000) + 900);                 // 15 min
  cachePut(k, longLived, now);
  assert.equal(cacheGet(k, now + 4 * 60_000), longLived);              // within 5 min (TOKEN_CACHE_MS)
  assert.equal(cacheGet(k, now + 6 * 60_000), null);                   // capped at 5 min even though the token lives 15
  const k2 = sessionKey("user-2:c");
  cachePut(k2, jwt(Math.floor(now / 1000) + 90), now);                 // 90 s left: usable for 30 s only (60 s safety margin)
  assert.ok(cacheGet(k2, now + 20_000));
  assert.equal(cacheGet(k2, now + 40_000), null);
  const k3 = sessionKey("user-3:c");
  cachePut(k3, jwt(Math.floor(now / 1000) + 30), now);                 // too close to expiry: not cached at all
  assert.equal(cacheGet(k3, now), null);
  cachePut(sessionKey("user-4:c"), "not-a-jwt", now);                  // unparseable token: not cached
  assert.equal(cacheGet(sessionKey("user-4:c"), now), null);
});

test("isUpstreamFailure separates a not-ready Neon Auth from a real 'no session' answer", async () => {
  const { isUpstreamFailure } = await import("./neon-auth.ts");
  assert.equal(isUpstreamFailure(null), false);
  assert.equal(isUpstreamFailure(undefined), false);
  assert.equal(isUpstreamFailure({ status: 401 }), false, "an invalid session is final");
  assert.equal(isUpstreamFailure({ status: 403 }), false);
  assert.equal(isUpstreamFailure({ status: 422 }), false);
  assert.equal(isUpstreamFailure({ status: 500 }), true);
  assert.equal(isUpstreamFailure({ status: 503 }), true);
  assert.equal(isUpstreamFailure({ message: "fetch failed" }), true, "no HTTP status means the call never completed");
});
