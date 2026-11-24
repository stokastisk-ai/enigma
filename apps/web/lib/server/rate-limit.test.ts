import assert from "node:assert/strict";
import { test } from "node:test";
import { clearForTests, clientIp, hit, reset } from "./rate-limit.ts";

test("fixed window allows max hits then blocks until the window ends", () => {
  clearForTests();
  const t = 5_000_000;
  for (let i = 0; i < 3; i++) assert.equal(hit("k", 3, 60_000, t + i).allowed, true);
  const blocked = hit("k", 3, 60_000, t + 10);
  assert.equal(blocked.allowed, false);
  assert.ok(blocked.retryAfterS >= 1 && blocked.retryAfterS <= 60);
  assert.equal(hit("k", 3, 60_000, t + 60_001).allowed, true);         // new window
  assert.equal(hit("other", 3, 60_000, t).allowed, true);              // keys are independent
});

test("reset clears a key so a successful sign-in does not keep the earlier typos against the user", () => {
  clearForTests();
  for (let i = 0; i < 5; i++) hit("acct", 5, 60_000, 1);
  assert.equal(hit("acct", 5, 60_000, 2).allowed, false);
  reset("acct");
  assert.equal(hit("acct", 5, 60_000, 3).allowed, true);
});

test("the limiter stays bounded under many distinct keys", () => {
  clearForTests();
  for (let i = 0; i < 6000; i++) hit(`flood-${i}`, 1, 60_000, 1);
  assert.equal(hit("flood-5999", 1, 60_000, 2).allowed, false);        // recent keys are still tracked
  assert.equal(hit("flood-0", 1, 60_000, 2).allowed, true);            // the oldest were evicted (no unbounded growth)
});

test("client address comes from the proxy-appended (last) X-Forwarded-For entry, not a client-supplied one", () => {
  const h = (m: Record<string, string>) => ({ get: (n: string) => m[n.toLowerCase()] ?? null });
  assert.equal(clientIp(h({ "x-forwarded-for": "6.6.6.6, 203.0.113.9" })), "203.0.113.9");
  assert.equal(clientIp(h({ "x-real-ip": "198.51.100.7" })), "198.51.100.7");
  assert.equal(clientIp(h({})), "unknown");
});
