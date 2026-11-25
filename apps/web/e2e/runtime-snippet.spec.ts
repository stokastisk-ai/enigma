import { expect, test } from "@playwright/test";
import { API, createWorkspace } from "./helpers";

// The connector calls FastAPI (/api/v1/...) directly. The generated setup snippet must point at the API origin (ENIGMA_PUBLIC_API_URL), never at the
// web app, and ENIGMA_MODE must follow the runtime's real mock/real status (a real key with mock mode is rejected by the API with 409 mock_mismatch).
test("the runtime setup snippet uses the public API origin and a mode that matches the runtime", async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await ctx.addCookies([{ name: "enigma_dev_user", value: "user-snippet", url: "http://127.0.0.1:3100" }]);
  await createWorkspace(page, "Snippet Co");
  await page.goto("/app/runtimes");

  // real (non-mock) runtime: the default
  await page.getByTestId("new-runtime").click();
  await page.getByLabel("Name").fill("real-gpu");
  await page.getByTestId("create-runtime").click();
  const dialog = page.getByTestId("token-dialog");
  await expect(dialog).toContainText(`os.environ["ENIGMA_API_URL"] = "${API}"`);
  await expect(dialog).toContainText('os.environ["ENIGMA_MODE"] = "stokastisk"');
  await expect(dialog).not.toContainText("127.0.0.1:3100");           // the web origin must never be offered as the API URL
  await expect(dialog).not.toContainText("YOUR-ENIGMA-API-HOST");
  await page.getByRole("button", { name: "I have saved the key" }).click();

  // mock runtime
  await page.getByTestId("new-runtime").click();
  await page.getByLabel("Name").fill("mock-one");
  await page.getByRole("switch", { name: /Mock connector/ }).click();
  await page.getByTestId("create-runtime").click();
  await expect(page.getByTestId("token-dialog")).toContainText('os.environ["ENIGMA_MODE"] = "mock"');
  await page.getByRole("button", { name: "I have saved the key" }).click();
  await ctx.close();
});
