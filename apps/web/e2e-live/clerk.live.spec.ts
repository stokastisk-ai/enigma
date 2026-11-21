import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { randomBytes } from "node:crypto";

const envFile = path.join(__dirname, "..", ".env.local");
test.skip(process.env.ENIGMA_LIVE_CLERK !== "1" || !fs.existsSync(envFile), "live Clerk test: set ENIGMA_LIVE_CLERK=1 and provide apps/web/.env.local");

const SK = (fs.existsSync(envFile) ? fs.readFileSync(envFile, "utf8").match(/^CLERK_SECRET_KEY=(.*)$/m)?.[1] ?? "" : "").replace(/^['"]|['"]$/g, "");
const clerk = async (method: string, p: string) => {
  for (let i = 0; ; i++) {                                         // bounded retry: this network occasionally times out on connect
    try { return await fetch(`https://api.clerk.com${p}`, { method, headers: { authorization: `Bearer ${SK}`, "user-agent": "enigma-live-test/0.1" } }); }
    catch (e) { if (i >= 3) throw e; await new Promise((r) => setTimeout(r, 2000 * (i + 1))); }
  }
};
const made: string[] = [];
const creds = (tag: string) => ({ email: `enigma-live-${tag}-${randomBytes(3).toString("hex")}+clerk_test@example.com`, password: `Zq-${randomBytes(12).toString("hex")}-Lm9!` });

test.afterAll(async () => {                                        // leave the dev instance tidy
  for (const email of made) {
    const r = await clerk("GET", `/v1/users?email_address=${encodeURIComponent(email)}`);
    for (const u of (await r.json()) as { id: string }[]) await clerk("DELETE", `/v1/users/${u.id}`);
  }
});

async function signUp(page: Page, tag: string) {
  const c = creds(tag);
  made.push(c.email);
  await page.goto("/sign-up");
  await page.getByLabel(/email address/i).fill(c.email);
  await page.getByLabel("Password", { exact: true }).fill(c.password);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  // Clerk dev instances accept the documented test code for +clerk_test addresses
  const code = page.getByRole("textbox", { name: /verification code/i });
  await code.waitFor({ timeout: 30_000 });
  await code.pressSequentially("424242", { delay: 80 });
  await page.waitForURL(/\/(onboarding|app)/, { timeout: 60_000 });
  return c;
}

test("real sign-up, workspace, BFF with a real session token, isolation, sign-out", async ({ browser }) => {
  const ctxA = await browser.newContext();
  const a = await ctxA.newPage();
  const credsA = await signUp(a, "a");
  await a.waitForURL("**/onboarding");                              // a brand-new user has no workspace yet
  await a.getByLabel("Workspace name").fill("Live Alpha");
  await a.getByRole("button", { name: "Create workspace" }).click();
  await a.waitForURL("**/app");
  await expect(a.getByTestId("dev-auth-banner")).toHaveCount(0);            // real Clerk mode: no dev banner
  const ws = (await (await a.request.get("/api/enigma/workspaces")).json()).items[0];
  expect(ws.name).toBe("Live Alpha");
  const pid = (await (await a.request.get(`/api/enigma/workspaces/${ws.id}/projects`)).json()).items[0].id;
  expect((await a.request.get(`/api/enigma/projects/${pid}/runtimes`)).status()).toBe(200);

  const ctxB = await browser.newContext();
  const b = await ctxB.newPage();
  await signUp(b, "b");
  await b.waitForURL("**/onboarding");
  expect((await (await b.request.get("/api/enigma/workspaces")).json()).items).toEqual([]);
  for (const u of [`/api/enigma/projects/${pid}/runtimes`, `/api/enigma/workspaces/${ws.id}/projects`, `/api/enigma/projects/${pid}/overview`]) {
    expect((await b.request.get(u)).status(), u).toBe(404);
  }

  // an unauthenticated browser is refused by both proxy and BFF
  const anon = await (await browser.newContext()).newPage();
  expect((await anon.request.get("/api/enigma/workspaces", { maxRedirects: 0 })).status()).not.toBe(200);
  await anon.goto("/app");
  await expect(anon).toHaveURL(/sign-in/);

  // sign out, wait for Clerk to finish (it navigates away from /app), then the same browser must be locked out everywhere
  await a.getByRole("button", { name: /open user menu|account/i }).first().click();
  await a.getByText(/^Sign out$/).click();
  await expect(a).not.toHaveURL(/\/app/, { timeout: 30_000 });
  const afterBff = await a.request.get("/api/enigma/workspaces", { maxRedirects: 0 });
  expect(afterBff.status(), "BFF must refuse a signed-out browser").not.toBe(200);
  await a.goto("/app");
  await expect(a).toHaveURL(/sign-in/);

  // sign back in: same user id, so the workspace created earlier is still there (durable identity -> data mapping)
  await a.getByLabel(/email address/i).fill(credsA.email);
  await a.getByRole("button", { name: "Continue", exact: true }).click();
  await a.getByLabel("Password", { exact: true }).fill(credsA.password);
  await a.getByRole("button", { name: "Continue", exact: true }).click();
  await a.waitForURL("**/app", { timeout: 60_000 });
  expect((await (await a.request.get("/api/enigma/workspaces")).json()).items[0].name).toBe("Live Alpha");
  await ctxA.close(); await ctxB.close();
});
