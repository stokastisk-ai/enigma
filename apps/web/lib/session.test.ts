import test from "node:test";
import assert from "node:assert/strict";
import { metricsPollMs, runtimeListPollMs, RUNTIME_POLL_IDLE_MS, RUNTIME_POLL_LIVE_MS } from "./session.ts";

test("metrics poll only while a runtime is live", () => {
  assert.equal(metricsPollMs("online", 5000), 5000);
  assert.equal(metricsPollMs("stale", 5000), 5000);
  for (const s of ["offline", "never_connected", undefined, null] as const) assert.equal(metricsPollMs(s, 5000), 0);
});

test("runtime list polls fast only while a runtime is live, slowly otherwise", () => {
  assert.equal(runtimeListPollMs(["offline", "online"]), RUNTIME_POLL_LIVE_MS);
  assert.equal(runtimeListPollMs(["offline", "never_connected"]), RUNTIME_POLL_IDLE_MS);
  assert.equal(runtimeListPollMs([]), RUNTIME_POLL_IDLE_MS);
  assert.ok(RUNTIME_POLL_IDLE_MS > RUNTIME_POLL_LIVE_MS);
});
