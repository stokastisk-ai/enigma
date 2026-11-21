import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { createWorkspace } from "./helpers";

test("no serious accessibility violations on key pages (desktop, dark and light)", async ({ page }) => {
  await page.context().addCookies([{ name: "enigma_dev_user", value: "user-a11y", url: "http://127.0.0.1:3100" }]);
  await createWorkspace(page, "A11y Lab");
  for (const scheme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.evaluate((s) => { try { localStorage.setItem("theme", s); } catch {} }, scheme);
    for (const path of ["/", "/app", "/app/playground", "/app/gpus", "/app/runtimes", "/app/settings/api-keys"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
      const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(bad.map((v) => `${scheme} ${path}: ${v.id} (${v.nodes.length}) ${v.nodes[0]?.target}`), `${scheme} ${path}`).toEqual([]);
    }
  }
});

test("mobile layout: navigation drawer works and nothing scrolls horizontally", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const page = await ctx.newPage();
  await ctx.addCookies([{ name: "enigma_dev_user", value: "user-a11y", url: "http://127.0.0.1:3100" }]);
  await page.goto("/app/playground");
  await page.getByRole("button", { name: "Toggle navigation" }).click();
  await expect(page.getByRole("link", { name: "GPUs" })).toBeVisible();
  await page.getByRole("link", { name: "GPUs" }).click();
  await page.waitForURL("**/app/gpus");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await ctx.close();
});
