import test from "node:test";
import assert from "node:assert/strict";
import { sameOrigin } from "./origin.ts";

test("same origin accepts the request host and the configured public host only", () => {
  assert.equal(sameOrigin("https://app.example.test", "app.example.test"), true);
  assert.equal(sameOrigin("https://app.example.test", "internal:3000", "app.example.test"), true);
  assert.equal(sameOrigin("https://evil.test", "app.example.test", "app.example.test"), false);
  assert.equal(sameOrigin(null, "app.example.test"), false);
  assert.equal(sameOrigin("not a url", "app.example.test"), false);
});
