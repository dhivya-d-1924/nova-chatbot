import { expect, test } from "@playwright/test";
import { login, noHorizontalOverflow, openSidebar, sendMessage } from "./helpers";

test("admin connects an internal app, tests an action and the assistant uses it", async ({ page }, info) => {
  const slug = `app${info.workerIndex}${Date.now() % 100000}`.slice(0, 20);
  await login(page, "admin@e2e.test");
  await expect(page.locator("#chat-input")).toBeVisible();
  const origin = new URL(page.url()).origin;
  await openSidebar(page);
  await page.getByRole("button", { name: "Admin dashboard" }).click();
  await page.getByRole("tab", { name: "Apps" }).click();
  await page.getByRole("button", { name: "Add an app" }).click();

  // the app: Nova's own health endpoint stands in for an internal system
  const form = page.getByRole("form", { name: "Add an app" });
  await form.getByLabel("Name", { exact: true }).fill(`Status ${slug}`);
  await form.getByLabel("Short name").fill(slug);
  await form.getByLabel("What it's for").fill("Server status checks");
  await form.getByLabel("Base URL").fill(`${origin}/api`);
  await form.getByLabel("Who can use it").selectOption("admin");
  await form.getByRole("button", { name: "Add app" }).click();
  const card = page.getByRole("region", { name: `Status ${slug}` });
  await expect(card).toBeVisible();

  await card.getByRole("button", { name: "Add action" }).click();
  const action = card.getByRole("form", { name: "Add an action" });
  await action.getByLabel("Action name").fill("health");
  await action.getByLabel("Label people see").fill("Check server health");
  await action.getByLabel("When to use it (for the assistant)").fill("Returns whether the server and AI are up");
  await action.getByLabel("Path", { exact: true }).fill("/health");
  await action.getByRole("button", { name: "Add action" }).click();
  await expect(card.getByText(`${slug}__health`)).toBeVisible();
  await noHorizontalOverflow(page);

  await card.getByRole("button", { name: "Test" }).click();
  await card.getByRole("button", { name: "Run test" }).click();
  await expect(card.getByText("HTTP 200")).toBeVisible();

  // the assistant calls it as a tool
  await page.getByRole("button", { name: /^(Back to chat|Chat)$/ }).click();
  await sendMessage(page, `app ${slug}__health {}`);
  await expect(page.getByText("Tool result:").last()).toBeVisible();
  await expect(page.getByText(/"status"/).last()).toBeVisible();

  // clean up (the app list is shared by every test using this server)
  await openSidebar(page);
  await page.getByRole("button", { name: "Admin dashboard" }).click();
  await page.getByRole("tab", { name: "Apps" }).click();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: `Delete Status ${slug}` }).click();
  await expect(page.getByRole("region", { name: `Status ${slug}` })).toHaveCount(0);
});
