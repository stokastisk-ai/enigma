import assert from "node:assert/strict";
import { test } from "node:test";
import { authConfig, publicSignupOpen, cacheClearForTests, cacheGet, cachePut, hasAuthCookie, requireVerified, sessionKey, toSdkError } from "./auth-server.ts";

const DSN = "postgresql://enigma_web_auth@db:5432/enigma";
const OK = { ENIGMA_AUTH: "on", ENIGMA_PUBLIC_ORIGIN: "https://enigma.example.test/", BETTER_AUTH_SECRET: "s".repeat(32), ENIGMA_AUTH_DB_DSN: DSN };
const jwt = (expSec: number) => `h.${Buffer.from(JSON.stringify({ exp: expSec })).toString("base64url")}.s`;

test("auth is off unless explicitly enabled and fully configured", () => {
  assert.deepEqual(authConfig(OK), { origin: "https://enigma.example.test", secret: "s".repeat(32), dbDsn: DSN, requireVerified: true });
  assert.equal(authConfig({ ...OK, ENIGMA_AUTH: undefined }), null);
  assert.equal(authConfig({ ...OK, ENIGMA_AUTH: "true" }), null);
  assert.equal(authConfig({ ...OK, BETTER_AUTH_SECRET: "short" }), null);
  assert.equal(authConfig({ ...OK, ENIGMA_AUTH_DB_DSN: "" }), null);
  assert.equal(authConfig({ ...OK, ENIGMA_AUTH_DB_DSN: "mysql://x@y/z" }), null);
  assert.equal(authConfig({ ...OK, ENIGMA_PUBLIC_ORIGIN: "http://enigma.example.test" }), null); // no cleartext public origin
  assert.equal(authConfig({ ...OK, ENIGMA_PUBLIC_ORIGIN: "https://u:p@enigma.example.test" }), null);
  assert.equal(authConfig({ ...OK, ENIGMA_PUBLIC_ORIGIN: "https://enigma.example.test/app" }), null);
  assert.equal(authConfig({ ...OK, ENIGMA_PUBLIC_ORIGIN: "not a url" }), null);
  assert.equal(authConfig({ ...OK, ENIGMA_PUBLIC_ORIGIN: "http://127.0.0.1:3000" })?.origin, "http://127.0.0.1:3000"); // local tests only
});

test("verified e-mail is required unless explicitly relaxed", () => {
  assert.equal(requireVerified({}), true);
  assert.equal(requireVerified({ ENIGMA_AUTH_REQUIRE_VERIFIED_EMAIL: "false" }), false);
  assert.equal(requireVerified({ ENIGMA_AUTH_REQUIRE_VERIFIED_EMAIL: "0" }), true);
});

test("only Enigma's own Better Auth cookie names count (presence, not trust)", () => {
  assert.equal(hasAuthCookie(["__Secure-enigma-auth.session_token"]), true);
  assert.equal(hasAuthCookie(["enigma-auth.session_data"]), true);
  assert.equal(hasAuthCookie(["neon-auth.session_token", "better-auth.session_token", "enigma-authx", "my-enigma-auth.session_token"]), false);
  assert.equal(hasAuthCookie([]), false);
});

test("Better Auth APIError maps to status+code; anything else is an upstream failure", () => {
  assert.deepEqual(toSdkError({ statusCode: 401, status: "UNAUTHORIZED", body: { code: "INVALID_EMAIL_OR_PASSWORD", message: "Invalid email or password" } }),
    { status: 401, code: "INVALID_EMAIL_OR_PASSWORD", message: "Invalid email or password" });
  assert.equal(toSdkError(new Error("connect ECONNREFUSED"))?.status, undefined);
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

test("isUpstreamFailure separates a not-ready auth database from a real 'no session' answer", async () => {
  const { isUpstreamFailure } = await import("./auth-server.ts");
  assert.equal(isUpstreamFailure(null), false);
  assert.equal(isUpstreamFailure(undefined), false);
  assert.equal(isUpstreamFailure({ status: 401 }), false, "an invalid session is final");
  assert.equal(isUpstreamFailure({ status: 403 }), false);
  assert.equal(isUpstreamFailure({ status: 422 }), false);
  assert.equal(isUpstreamFailure({ status: 500 }), true);
  assert.equal(isUpstreamFailure({ status: 503 }), true);
  assert.equal(isUpstreamFailure({ message: "fetch failed" }), true, "no HTTP status means the call never completed");
});

test("public sign-up is closed unless explicitly opened (private beta)", () => {
  assert.equal(publicSignupOpen({}), false);
  assert.equal(publicSignupOpen({ ENIGMA_PUBLIC_SIGNUP: "true" }), false);
  assert.equal(publicSignupOpen({ ENIGMA_PUBLIC_SIGNUP: "on" }), true);
});
