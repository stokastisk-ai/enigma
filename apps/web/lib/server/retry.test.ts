import test from "node:test";
import assert from "node:assert/strict";
import { isTransientStatus, withRetries } from "./retry.ts";

const noSleep = async () => {};

test("retries a throwing call until it succeeds, within the attempt budget", async () => {
  let n = 0;
  const r = await withRetries(async () => { if (++n < 3) throw new Error("ECONNREFUSED"); return "ok"; }, () => false, { attempts: 4, delaysMs: [1], sleep: noSleep });
  assert.equal(r, "ok");
  assert.equal(n, 3);
});

test("gives up after the last attempt and rethrows the final error", async () => {
  let n = 0;
  await assert.rejects(withRetries(async () => { n++; throw new Error("down"); }, () => false, { attempts: 3, delaysMs: [1], sleep: noSleep }), /down/);
  assert.equal(n, 3);
});

test("a single attempt never retries (non-idempotent writes use attempts: 1)", async () => {
  let n = 0;
  await assert.rejects(withRetries(async () => { n++; throw new Error("x"); }, () => true, { attempts: 1, delaysMs: [1], sleep: noSleep }));
  assert.equal(n, 1);
});

test("retries transient statuses but returns a real answer immediately", async () => {
  const seq = [502, 503, 200];
  let i = 0;
  const r = await withRetries(async () => seq[i++], isTransientStatus, { attempts: 5, delaysMs: [1], sleep: noSleep });
  assert.equal(r, 200);
  assert.equal(i, 3);
  let j = 0;
  assert.equal(await withRetries(async () => (j++, 404), isTransientStatus, { attempts: 5, delaysMs: [1], sleep: noSleep }), 404);
  assert.equal(j, 1, "a 404 is an answer, not a startup problem");
});

test("backoff uses the configured delays and repeats the last one", async () => {
  const waited: number[] = [];
  await withRetries(async () => 502, isTransientStatus, { attempts: 5, delaysMs: [100, 200], sleep: async (ms) => { waited.push(ms); } });
  assert.deepEqual(waited, [100, 200, 200, 200]);
});

test("only 502, 503 and 504 count as transient", () => {
  for (const s of [502, 503, 504]) assert.equal(isTransientStatus(s), true);
  for (const s of [200, 400, 401, 403, 404, 409, 422, 429, 500]) assert.equal(isTransientStatus(s), false);
});
