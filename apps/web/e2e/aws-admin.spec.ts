import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type Page } from "@playwright/test";

// The API runs with ENIGMA_AWS_OPS=mock: every AWS value here is SYNTHETIC and no AWS call is ever made.
const ADMIN = "user-aws-admin";
const as = (page: Page, user: string) => page.context().addCookies([{ name: "enigma_dev_user", value: user, url: "http://127.0.0.1:3100" }]);
const ROUTES = ["/api/enigma/admin/aws/status", "/api/enigma/admin/aws/metrics?range=1h", "/api/enigma/admin/aws/cost"];

/** The API keeps state for the whole run, so a user may already have a workspace from an earlier test: create one only if none exists. */
async function signedIn(browser: Browser, user: string, ws: string) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await as(page, user);
  const existing = await (await page.request.get("/api/enigma/workspaces")).json();
  if (existing.items.length === 0) expect((await page.request.post("/api/enigma/workspaces", { data: { name: ws } })).status()).toBe(201);
  await page.goto("/app");
  await expect(page.getByTestId("workspace-picker")).toBeVisible();
  return { ctx, page };
}

test("the platform admin sees the AWS Infrastructure page, clearly labelled SYNTHETIC", async ({ browser }) => {
  const { ctx, page } = await signedIn(browser, ADMIN, "Admin Co");
  await expect(page.getByRole("link", { name: "AWS Infrastructure" })).toBeVisible();
  await page.goto("/app/admin/aws");
  await expect(page.getByRole("heading", { name: "AWS Infrastructure" })).toBeVisible();
  await expect(page.getByTestId("aws-state")).toHaveAttribute("data-state", "SYNTHETIC");
  await expect(page.getByTestId("synthetic-badge").first()).toContainText("SYNTHETIC");
  await expect(page.getByTestId("aws-provenance").first()).toHaveAttribute("data-source", "synthetic");
  await expect(page.getByTestId("aws-boundary")).toContainText("Kaggle");
  await expect(page.getByTestId("aws-boundary")).toContainText("GPU");
  await expect(page.getByTestId("aws-build")).toContainText("e2e0000");
  await expect(page.getByTestId("aws-api-health")).toContainText("ok");
  // inventory: every resource carries the Synthetic badge, never "Deployed"
  const statuses = await page.getByTestId("resource-status").evaluateAll((els) => els.map((e) => e.getAttribute("data-status")));
  expect(statuses.length).toBe(5);
  expect(new Set(statuses)).toEqual(new Set(["SYNTHETIC"]));
  await expect(page.getByTestId("aws-resource-count")).toContainText("synthetic");      // never "5 / 5 deployed" for demo data
  const prov = await page.getByTestId("aws-provenance").first().innerText();
  expect(prov).not.toMatch(/ago ago/);                                                   // fmtAge already says "ago"
  await expect(page.getByTestId("aws-api-health")).not.toContainText("ago");              // uptime is a duration, not an age
  // topology separates Kaggle (not AWS) from the AWS control plane
  await expect(page.getByTestId("topo-kaggle")).toContainText("not AWS");
  await expect(page.getByTestId("topo-ddb")).not.toContainText("not AWS");
  // charts render from the synthetic series
  await expect(page.getByTestId("aws-charts")).toBeVisible();
  for (const id of ["web-traffic", "api-latency", "ddb-capacity", "cf-errors"]) await expect(page.getByTestId(`aws-chart-${id}`)).toBeVisible();
  // cost panel: link-out only, safe link attributes, expiry stated as UNKNOWN, no bill figures
  const cost = page.getByTestId("aws-cost");
  await expect(cost).toContainText("UNKNOWN");
  for (const a of await cost.getByRole("link").all()) {
    await expect(a).toHaveAttribute("target", "_blank");
    await expect(a).toHaveAttribute("rel", /noopener/);
    expect(await a.getAttribute("href")).toMatch(/^https:\/\/console\.aws\.amazon\.com\//);
  }
  expect(await cost.innerText()).not.toMatch(/\$\s?\d/);
  // nothing that looks like an ARN, account id or key
  const text = await page.getByTestId("aws-page").innerText();
  expect(text).not.toMatch(/arn:aws|\b\d{12}\b|AKIA[0-9A-Z]{16}/);
  await ctx.close();
});

test("a non-admin cannot see, open or call the AWS Infrastructure feature", async ({ browser }) => {
  const { ctx, page } = await signedIn(browser, "user-not-admin", "Other Co");
  await expect(page.getByRole("link", { name: "AWS Infrastructure" })).toHaveCount(0);
  await page.goto("/app/admin/aws");
  await expect(page.getByText("Not authorised")).toBeVisible();
  await expect(page.getByTestId("aws-page")).toHaveCount(0);
  for (const url of ROUTES) {
    const r = await page.request.get(url);
    expect(r.status(), url).toBe(403);
    expect(await r.text()).not.toMatch(/synthetic-|SYNTHETIC|resources|Lambda|DynamoDB/);   // the denial leaks nothing about the inventory
  }
  // only GET exists; nothing writable is exposed through the BFF
  expect((await page.request.post("/api/enigma/admin/aws/status", { data: {} })).status()).toBe(404);
  expect((await page.request.get("/api/enigma/admin/aws/status?evil=1")).status()).toBe(422);
  expect((await page.request.get("/api/enigma/admin/aws/metrics?range=7d")).status()).toBe(403);   // authorisation is evaluated BEFORE validation: no probing by a non-admin
  expect((await page.request.get("/api/enigma/admin/aws/../../prune")).status()).toBe(404);
  await ctx.close();
});

test("the admin gets validation errors for bad ranges and the BFF stays read-only", async ({ browser }) => {
  const { ctx, page } = await signedIn(browser, ADMIN, "Admin Valid");
  expect((await page.request.get("/api/enigma/admin/aws/metrics?range=7d")).status()).toBe(422);
  expect((await page.request.get("/api/enigma/admin/aws/metrics?range=6h")).status()).toBe(200);
  for (const m of ["post", "put", "delete"] as const) expect([404, 405], m).toContain((await page.request[m]("/api/enigma/admin/aws/status", { data: {} })).status());   // 404 (BFF allow-list) or 405 (method not exported): never forwarded
  await ctx.close();
});

test("two users at the same time: the admin sees data, the other user is denied, and neither affects the other", async ({ browser }) => {
  const a = await signedIn(browser, ADMIN, "Admin Two");
  const b = await signedIn(browser, "user-second", "Second Two");
  const [ra, rb] = await Promise.all([a.page.request.get(ROUTES[0]), b.page.request.get(ROUTES[0])]);
  expect(ra.status()).toBe(200);
  expect(rb.status()).toBe(403);
  expect((await ra.json()).provenance.synthetic).toBe(true);
  expect(await rb.text()).not.toContain((await ra.json()).app.build_sha);
  await a.ctx.close(); await b.ctx.close();
});

test("the page does not poll AWS-bound endpoints at a high rate and the range control re-queries once", async ({ browser }) => {
  const { ctx, page } = await signedIn(browser, ADMIN, "Admin Rate");
  const hits: string[] = [];
  page.on("request", (r) => { if (r.url().includes("/api/enigma/admin/aws/")) hits.push(new URL(r.url()).pathname + new URL(r.url()).search); });
  await page.goto("/app/admin/aws");
  await expect(page.getByTestId("aws-charts")).toBeVisible();
  await page.waitForTimeout(4000);                                            // a 1-second poller would have made 4+ more calls by now
  expect(hits.filter((h) => h.endsWith("/status")).length).toBeLessThanOrEqual(2);
  expect(hits.filter((h) => h.includes("/metrics?range=1h")).length).toBeLessThanOrEqual(2);
  const before = hits.length;
  await page.getByTestId("aws-range").click();
  await page.getByRole("option", { name: "Last 6h" }).click();
  await expect.poll(() => hits.filter((h) => h.includes("range=6h")).length).toBeGreaterThanOrEqual(1);
  expect(hits.length - before).toBeLessThanOrEqual(3);
  await ctx.close();
});

test("the AWS page has no serious accessibility violations (dark and light)", async ({ browser }) => {
  const { ctx, page } = await signedIn(browser, ADMIN, "Admin A11y");
  for (const scheme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.evaluate((s) => { try { localStorage.setItem("theme", s); } catch {} }, scheme);
    await page.goto("/app/admin/aws");
    await expect(page.getByTestId("aws-charts")).toBeVisible();
    await page.waitForLoadState("networkidle");
    const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${scheme}: ${v.id} (${v.nodes.length}) ${v.nodes[0]?.target}`), scheme).toEqual([]);
  }
  await ctx.close();
});
