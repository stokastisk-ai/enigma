import assert from "node:assert/strict";
import { test } from "node:test";
import { passwordProblem } from "./password-policy.ts";

const E = "waqas.example@example.test";

test("acceptable passwords pass (length-based, no composition rules)", () => {
  assert.equal(passwordProblem(E, "correct horse battery staple"), null);
  assert.equal(passwordProblem(E, "k7#Vq2!mZp9x"), null);
  assert.equal(passwordProblem(E, "a".repeat(5) + "bcdefghij12"), null);
});

test("too short, too long, low variety and repeating patterns are refused", () => {
  assert.match(passwordProblem(E, "Short1!")!, /at least 12/);
  assert.match(passwordProblem(E, "x".repeat(129))!, /at most 128/);
  assert.ok(passwordProblem(E, "aaaaaaaaaaaaaa"));
  assert.ok(passwordProblem(E, "ababababababab"));
  assert.ok(passwordProblem(E, "abcabcabcabcabc"));
});

test("common passwords and the e-mail name are refused", () => {
  assert.match(passwordProblem(E, "Password1234")!, /common/);
  assert.match(passwordProblem(E, "123456789012")!, /common/);
  assert.match(passwordProblem(E, "my-waqas.example-pw9")!, /email name/);
  assert.equal(passwordProblem("ab@example.test", "xxab-secret-pw99"), null);   // a 2-character local part is too short to be a useful check
});

test("the problem text never contains the password", () => {
  const pw = "Password1234";
  assert.ok(!passwordProblem(E, pw)!.includes(pw));
});
