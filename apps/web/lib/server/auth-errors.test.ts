import assert from "node:assert/strict";
import { test } from "node:test";
import * as c from "./auth-errors.ts";

const e = (status: number, code = "", message = "") => ({ status, code, message });

test("only real credential failures are reported as invalid credentials", () => {
  assert.equal(c.signIn(e(401, "INVALID_EMAIL_OR_PASSWORD", "Invalid email or password")).code, "invalid_credentials");
  assert.equal(c.signIn(e(400, "USER_NOT_FOUND")).status, 401);
});

test("an origin rejection, an outage or a network error is NOT shown as bad credentials", () => {
  for (const err of [e(403, "INVALID_ORIGIN", "Invalid origin"), e(500), e(503, "X"), e(0, "NETWORK_DNS"), e(403)]) {
    const o = c.signIn(err);
    assert.equal(o.code, "auth_unavailable", JSON.stringify(err));
    assert.equal(o.upstream, true);
    assert.equal(o.status, 502);
  }
});

test("an unverified account asks for verification, a throttled one says wait", () => {
  const o = c.signIn(e(403, "EMAIL_NOT_VERIFIED", "Email not verified"));
  assert.equal(o.needsVerify, true);
  assert.equal(o.status, 403);
  assert.equal(c.signIn(e(429)).status, 429);
});

test("sign-up: existing and fresh addresses are indistinguishable; real input problems are specific", () => {
  assert.equal(c.signUp(e(422, "USER_ALREADY_EXISTS", "User already exists")), "accepted");
  assert.equal(c.signUp(e(422, "USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL")), "accepted");
  assert.equal((c.signUp(e(400, "PASSWORD_TOO_SHORT")) as c.Outcome).code, "weak_password");
  assert.equal((c.signUp(e(400, "INVALID_EMAIL")) as c.Outcome).code, "invalid_email");
  assert.equal((c.signUp(e(403, "INVALID_ORIGIN")) as c.Outcome).code, "auth_unavailable");
  assert.equal((c.signUp(e(500)) as c.Outcome).upstream, true);
});

test("verify: invalid, expired, too many attempts and already-verified each get a useful, distinct message", () => {
  assert.equal(c.verify(e(400, "INVALID_OTP", "Invalid OTP")).code, "invalid_code");
  assert.equal(c.verify(e(400, "OTP_EXPIRED", "OTP expired")).code, "expired_code");
  assert.equal(c.verify(e(400, "", "The code has expired")).code, "expired_code");
  assert.equal(c.verify(e(403, "TOO_MANY_ATTEMPTS", "Too many attempts")).code, "too_many_attempts");
  assert.equal(c.verify(e(400, "EMAIL_ALREADY_VERIFIED")).code, "already_verified");
  assert.equal(c.verify(e(502)).code, "auth_unavailable");
});

test("resend looks identical for unknown/verified accounts but surfaces real outages and throttling", () => {
  assert.equal(c.resend(e(400, "USER_NOT_FOUND")), "accepted");
  assert.equal(c.resend(e(400, "EMAIL_ALREADY_VERIFIED")), "accepted");
  assert.equal((c.resend(e(429)) as c.Outcome).status, 429);
  assert.equal((c.resend(e(403, "INVALID_ORIGIN")) as c.Outcome).code, "auth_unavailable");
});

test("no public message echoes upstream text", () => {
  const o = c.signIn(e(401, "INVALID_EMAIL_OR_PASSWORD", "secret-upstream-detail"));
  assert.ok(!o.message.includes("secret-upstream-detail"));
});
