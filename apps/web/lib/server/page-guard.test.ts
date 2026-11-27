import assert from "node:assert/strict";
import { test } from "node:test";
import { requireSessionOr } from "./page-guard-core.ts";

class Denied extends Error {}
const deny = (): never => {
  throw new Denied("redirect:/sign-in"); // stands in for next/navigation redirect(), which also throws
};

test("an anonymous visitor is denied by the layout check itself, even if the proxy was skipped", async () => {
  await assert.rejects(requireSessionOr(async () => null, deny), Denied);
});

test("a lookup failure is not treated as a session (it propagates, so the page cannot render)", async () => {
  await assert.rejects(requireSessionOr(async () => { throw new Error("verifier down"); }, deny), /verifier down/);
});

test("a verified session is returned unchanged and deny() is not called", async () => {
  const s = { userId: "u1", token: "t", mode: "betterauth" as const };
  let denied = false;
  const got = await requireSessionOr(async () => s, () => { denied = true; return deny(); });
  assert.equal(got, s);
  assert.equal(denied, false);
});
