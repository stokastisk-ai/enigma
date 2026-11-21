import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import type { Page } from "@playwright/test";

export const API = "http://127.0.0.1:8787";
const connectorDir = path.resolve(__dirname, "../../../connector");

export function startConnector(token: string, delay = "0.02"): ChildProcess {
  return spawn("/usr/local/bin/python3.13", ["-m", "enigma_connector", "--mode", "mock"], {
    cwd: connectorDir,
    env: { ...process.env, ENIGMA_API_URL: API, ENIGMA_RUNTIME_TOKEN: token, ENIGMA_INSTANCE: `e2e-${Date.now() % 100000}`, ENIGMA_MOCK_TOKEN_DELAY_S: delay, ENIGMA_HEARTBEAT_S: "1.5", PYTHONDONTWRITEBYTECODE: "1", ENIGMA_FORBID_LOCAL_GPU: "1" },
    stdio: "ignore",
  });
}

export async function createWorkspace(page: Page, name: string) {
  await page.goto("/app");
  await page.waitForURL("**/onboarding");
  await page.getByLabel("Workspace name").fill(name);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.waitForURL("**/app");
}

export async function createMockRuntime(page: Page, name: string): Promise<string> {
  await page.goto("/app/runtimes");
  await page.getByTestId("new-runtime").click();
  await page.getByLabel("Name").fill(name);
  await page.getByRole("switch", { name: /Mock connector/ }).click();
  await page.getByTestId("create-runtime").click();
  await page.getByRole("button", { name: "Reveal" }).click();
  const token = (await page.getByTestId("token-value").innerText()).trim();
  await page.getByRole("button", { name: "I have saved the key" }).click();
  return token;
}
