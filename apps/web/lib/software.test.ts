import assert from "node:assert/strict";
import { test } from "node:test";
import { softwareRows, softwareText } from "./software.ts";

const SHA = "fe80d7c36fa78cf8a26feb1e00bb5f1f006ca45a027450353603976320ae0b3e";

test("null, empty and the literal string 'null' read 'not reported', never 'null'", () => {
  const t = softwareText({ stokastisk_version: "0.1.0", vllm_version: null, python_version: "null", stokastisk_build: "", driver_version: "580.178.04" });
  assert.ok(!/\bnull\b/.test(t), t);
  assert.match(t, /vLLM not reported/);
  assert.match(t, /Python not reported/);
  assert.match(t, /build not reported/);
  assert.match(t, /Stokastisk 0\.1\.0/);
  assert.match(t, /NVIDIA driver 580\.178\.04/);
});

test("a fully reported identity is shown in a stable order with a shortened hash and the full hash as the tooltip", () => {
  const rows = softwareRows({ wheel_sha256: SHA, python_version: "3.13.16", vllm_version: "0.25.1", stokastisk_build: "5", stokastisk_version: "0.1.0" });
  assert.deepEqual(rows.map((r) => r.key), ["stokastisk_version", "stokastisk_build", "vllm_version", "python_version", "driver_version", "wheel_sha256"]);
  const w = rows.find((r) => r.key === "wheel_sha256")!;
  assert.equal(w.value, `${SHA.slice(0, 12)}…`);
  assert.equal(w.title, SHA);
});

test("an absent identity (older runtimes) is all 'not reported' and unknown extra fields are still listed", () => {
  assert.equal(softwareRows(undefined).every((r) => r.value === null), true);
  assert.ok(softwareRows({ flavour: "x" }).some((r) => r.key === "flavour" && r.value === "x"));
});
