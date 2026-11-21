import { expect, test } from "@playwright/test";
import type { ChildProcess } from "node:child_process";
import { createMockRuntime, createWorkspace, startConnector } from "./helpers";

test.describe.configure({ mode: "serial" });

let token = "";
let conn: ChildProcess | null = null;
test.afterAll(() => { conn?.kill("SIGTERM"); });

test("onboarding, mock runtime, one-time key", async ({ page }) => {
  await createWorkspace(page, "E2E Lab");
  await expect(page.getByTestId("dev-auth-banner")).toBeVisible();          // the dev login is always labelled
  token = await createMockRuntime(page, "mock-t4x2");
  expect(token).toMatch(/^enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{43}$/);
  await page.reload();
  await expect(page.getByTestId("runtime-mock-t4x2")).toContainText("Never connected");
  await expect(page.getByTestId("runtime-mock-t4x2").getByTestId("mock-badge")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(token);               // the key is never shown again
});

test("connector goes online; streaming chat persists with measured diagnostics", async ({ page }) => {
  conn = startConnector(token, "0.03");
  await page.goto("/app/playground");
  await expect(page.getByTestId("runtime-status").first()).toHaveAttribute("data-status", "online", { timeout: 30_000 });
  await page.getByTestId("prompt-input").fill("Explain what a tensor core is");
  await page.getByTestId("prompt-input").press("Enter");
  await expect(page.getByTestId("msg-live")).toBeVisible();
  await expect(page.getByTestId("msg-assistant").last()).toContainText("[MOCK MODEL", { timeout: 30_000 });
  await expect(page.getByTestId("measured-line").last()).toContainText("TTFT");
  await expect(page.getByTestId("diag-ttft")).not.toHaveText("N/A");
  await expect(page.getByTestId("diagnostics")).toContainText("Synthetic (mock)");
  await expect(page.getByTestId("transport")).toBeVisible();
  await page.reload();                                                       // history is durable
  await expect(page.getByTestId("msg-user").first()).toContainText("tensor core");
  await expect(page.getByTestId("msg-assistant").first()).toContainText("[MOCK MODEL");
});

test("Stop cancels a streaming reply and keeps the partial text", async ({ page }) => {
  await page.goto("/app/playground");
  await page.getByTestId("prompt-input").fill("word ".repeat(30));
  await page.getByTestId("prompt-input").press("Enter");
  await expect(page.getByTestId("msg-live")).toContainText("MOCK", { timeout: 20_000 });
  await page.getByTestId("cancel-button").click();
  await expect(page.getByText("Stopped early")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("send-button")).toBeVisible();
});

test("GPU and vLLM pages show per-GPU data labelled synthetic", async ({ page }) => {
  await page.goto("/app/gpus");
  await expect(page.getByTestId("gpu-card-0")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("gpu-card-1")).toBeVisible();
  await expect(page.getByTestId("provenance").first()).toHaveAttribute("data-kind", "mock");
  await expect(page.getByTestId("gpu-card-0")).toContainText("N/A");           // PCIe is not reported by the mock: N/A, never 0
  await page.goto("/app/inference");
  await expect(page.getByTestId("provenance").first()).toHaveAttribute("data-kind", "mock", { timeout: 30_000 });
  await expect(page.getByTestId("chart-throughput")).toBeVisible();
  await page.goto("/app/runs");
  await expect(page.getByTestId("runs-table")).toBeVisible();
  await page.getByRole("checkbox").first().check();
  await expect(page.getByTestId("comparison")).toBeVisible();
});

test("offline is detected, chat is disabled, and reconnect recovers", async ({ page }) => {
  conn?.kill("SIGTERM"); conn = null;
  await page.goto("/app/playground");
  await expect(page.getByTestId("runtime-status").first()).toHaveAttribute("data-status", "offline", { timeout: 40_000 });
  await expect(page.getByTestId("offline-banner")).toBeVisible();
  await expect(page.getByTestId("prompt-input")).toBeDisabled();
  await expect(page.getByTestId("msg-assistant").first()).toBeVisible();     // history still readable offline
  conn = startConnector(token);
  await expect(page.getByTestId("runtime-status").first()).toHaveAttribute("data-status", "online", { timeout: 40_000 });
  await expect(page.getByTestId("prompt-input")).toBeEnabled();
});

test("rotating the key shows the new key once and disconnects the old connector", async ({ page }) => {
  await page.goto("/app/settings/api-keys");
  await page.getByTestId("rotate-key").click();
  await page.getByTestId("confirm-key-action").click();
  await page.getByRole("button", { name: "Reveal" }).click();
  const fresh = (await page.getByTestId("token-value").innerText()).trim();
  expect(fresh).not.toBe(token);
  await page.getByRole("button", { name: "I have saved the key" }).click();
  const r = await page.request.post("http://127.0.0.1:8787/api/v1/jobs/claim", { data: {}, headers: { authorization: `Bearer ${token}` } });
  expect(r.status()).toBe(401);                                              // the old key is dead immediately
  conn?.kill("SIGTERM"); conn = null;
});
