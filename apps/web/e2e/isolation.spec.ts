import { expect, test } from "@playwright/test";
import { createWorkspace } from "./helpers";

const as = (page: import("@playwright/test").Page, user: string) =>
  page.context().addCookies([{ name: "enigma_dev_user", value: user, url: "http://127.0.0.1:3100" }]);

test("a second user cannot see or reach the first user's workspace", async ({ browser }) => {
  const a = await browser.newContext();
  const pa = await a.newPage();
  await as(pa, "user-iso-a");
  await createWorkspace(pa, "Alpha Co");
  const alpha = await pa.request.get("/api/enigma/workspaces");
  const ws = (await alpha.json()).items[0];
  const projects = await (await pa.request.get(`/api/enigma/workspaces/${ws.id}/projects`)).json();
  const pid = projects.items[0].id;
  expect((await pa.request.get(`/api/enigma/projects/${pid}/runtimes`)).status()).toBe(200);

  const b = await browser.newContext();
  const pb = await b.newPage();
  await as(pb, "user-iso-b");
  await pb.goto("/app");
  await pb.waitForURL("**/onboarding");                                      // B has no workspace
  expect((await (await pb.request.get("/api/enigma/workspaces")).json()).items).toEqual([]);
  for (const url of [`/api/enigma/projects/${pid}/runtimes`, `/api/enigma/workspaces/${ws.id}/projects`, `/api/enigma/projects/${pid}/metrics/gpu`, `/api/enigma/projects/${pid}/overview`]) {
    expect((await pb.request.get(url)).status(), url).toBe(404);
  }
  await a.close(); await b.close();
});

test("the BFF only forwards allow-listed routes and validates bodies", async ({ page }) => {
  await page.context().addCookies([{ name: "enigma_dev_user", value: "user-bff", url: "http://127.0.0.1:3100" }]);
  expect((await page.request.get("/api/enigma/admin/prune")).status()).toBe(404);
  expect((await page.request.get("/api/enigma/..%2F..%2Fhealth")).status()).toBe(404);
  expect((await page.request.post("/api/enigma/workspaces", { data: { name: "x", evil: 1 } })).status()).toBe(422);
  expect((await page.request.get("/api/enigma/workspaces?evil=1")).status()).toBe(422);
  const conv = "00000000-0000-0000-0000-000000000000";
  const r = await page.request.post(`/api/enigma/conversations/${conv}/jobs`, { data: { runtime_id: conv, content: "SECRET-PROMPT" } });
  expect(r.status()).toBe(422);                                              // missing Idempotency-Key
  expect(await r.text()).not.toContain("SECRET-PROMPT");
});
