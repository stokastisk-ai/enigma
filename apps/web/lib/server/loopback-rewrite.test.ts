import assert from "node:assert/strict";
import { test } from "node:test";
import { dropLoopbackSelfRewrite, isLoopbackHostname } from "./loopback-rewrite.ts";

const withRewrite = (to: string, extra: Record<string, string> = {}) => new Response(null, { headers: { "x-middleware-rewrite": to, ...extra } });

test("the Clerk same-URL rewrite on loopback becomes a plain continue and keeps the header overrides", () => {
  const res = withRewrite("http://localhost:3000/", { "x-middleware-override-headers": "x-clerk-auth-status", "x-middleware-request-x-clerk-auth-status": "signed-out" });
  dropLoopbackSelfRewrite({ url: "http://localhost:3000/" }, res);
  assert.equal(res.headers.get("x-middleware-rewrite"), null);
  assert.equal(res.headers.get("x-middleware-next"), "1");
  assert.equal(res.headers.get("x-middleware-override-headers"), "x-clerk-auth-status");
  assert.equal(res.headers.get("x-middleware-request-x-clerk-auth-status"), "signed-out");
});

test("the same holds for 127.0.0.1, ::1 and a query string", () => {
  for (const host of ["127.0.0.1", "127.1.2.3", "[::1]", "localhost"]) {
    const res = withRewrite(`http://${host}:3000/app/gpus?x=1`);
    dropLoopbackSelfRewrite({ url: `http://${host}:3000/app/gpus?x=1` }, res);
    assert.equal(res.headers.get("x-middleware-rewrite"), null, host);
  }
});

test("a rewrite to a different path, port, scheme or query is NOT touched", () => {
  const req = { url: "http://localhost:3000/app" };
  for (const to of ["http://localhost:3000/other", "http://localhost:3001/app", "https://localhost:3000/app", "http://localhost:3000/app?a=1"]) {
    const res = withRewrite(to);
    dropLoopbackSelfRewrite(req, res);
    assert.equal(res.headers.get("x-middleware-rewrite"), to);
    assert.equal(res.headers.get("x-middleware-next"), null);
  }
});

test("a rewrite to a non-loopback host is never removed (nothing here can hide an external destination)", () => {
  for (const to of ["https://evil.example/", "http://10.0.0.5:3000/", "http://localhost.evil.example:3000/", "http://127.0.0.1.evil.example:3000/"]) {
    const res = withRewrite(to);
    dropLoopbackSelfRewrite({ url: to }, res); // even if request.url lies and matches, a non-loopback target stays visible to Next
    assert.equal(res.headers.get("x-middleware-rewrite"), to, to);
  }
  const res = withRewrite("https://app.example.com/app");
  dropLoopbackSelfRewrite({ url: "https://app.example.com/app" }, res); // production hosts: unchanged behaviour
  assert.equal(res.headers.get("x-middleware-rewrite"), "https://app.example.com/app");
});

test("responses without a rewrite, empty results and unparsable URLs pass through", () => {
  const plain = new Response(null, { headers: { "x-middleware-next": "1" } });
  assert.equal(dropLoopbackSelfRewrite({ url: "http://localhost:3000/" }, plain), plain);
  assert.equal(dropLoopbackSelfRewrite({ url: "http://localhost:3000/" }, undefined), undefined);
  assert.equal(dropLoopbackSelfRewrite({ url: "http://localhost:3000/" }, null), null);
  const bad = withRewrite("not a url");
  dropLoopbackSelfRewrite({ url: "http://localhost:3000/" }, bad);
  assert.equal(bad.headers.get("x-middleware-rewrite"), "not a url");
});

test("isLoopbackHostname is anchored", () => {
  for (const ok of ["localhost", "127.0.0.1", "127.255.255.255", "[::1]"]) assert.ok(isLoopbackHostname(ok), ok);
  for (const bad of ["localhost.evil.example", "evil-localhost", "128.0.0.1", "127.0.0.1.nip.io", "127.0.0", "", "10.0.0.1"]) assert.ok(!isLoopbackHostname(bad), bad);
});
