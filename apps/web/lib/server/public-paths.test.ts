import assert from "node:assert/strict";
import { test } from "node:test";
import { isPublicPath } from "./public-paths.ts";

test("only the landing page and the sign-in/sign-up catch-alls are public", () => {
  for (const p of ["/", "/sign-in", "/sign-in/", "/sign-up", "/sign-in/factor-one", "/sign-up/verify-email-address/x"]) assert.ok(isPublicPath(p), p);
});

test("everything else — including look-alikes the old regex-style matcher accepted — is protected", () => {
  for (const p of ["/sign-industries", "/sign-in-evil", "/sign-upgrade", "/sign-out", "/app", "/app/gpus", "/onboarding", "/api/enigma/gpus", "/api/auth/sign-in", "/_next/x", "/signin", ""]) {
    assert.ok(!isPublicPath(p), p);
  }
});

test("ambiguous encodings, backslashes and doubled slashes are never public", () => {
  for (const p of ["/%73ign-in", "/sign-in%2f..%2fapp", "/sign-in/%2e%2e/app", "//sign-in", "/sign-in//x", "/sign-in\\..\\app", "/app//sign-in"]) {
    assert.ok(!isPublicPath(p), p);
  }
});

test("a protected prefix is not made public by a later sign-in segment", () => {
  assert.ok(!isPublicPath("/app/sign-in"));
  assert.ok(!isPublicPath("/onboarding/sign-up"));
});
