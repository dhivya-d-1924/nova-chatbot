import { expect, test } from "@playwright/test";
import { openSidebar, register, sendMessage, uniqueEmail } from "./helpers";

test.beforeEach(async ({ page }, info) => { await register(page, uniqueEmail(info)); });

test("streams a reply with timestamp, copy and regenerate", async ({ page }) => {
  await sendMessage(page, "hello streaming world");
  await expect(page.getByText("Echo: hello streaming world")).toBeVisible();
  await expect(page.locator("time").first()).toBeVisible();
  await page.getByRole("button", { name: "Regenerate response" }).click();
  await expect(page.getByText("Echo: hello streaming world")).toHaveCount(1);
});

test("edit an earlier message replaces the later replies", async ({ page }) => {
  await sendMessage(page, "first question");
  await expect(page.getByText("Echo: first question")).toBeVisible();
  await page.getByRole("log").getByText("first question", { exact: true }).hover();
  await page.getByRole("button", { name: "Edit message" }).click();
  const box = page.getByLabel("Edit your message");
  await box.fill("better question");
  await page.getByRole("button", { name: "Save & send" }).click();
  await expect(page.getByText("Echo: better question")).toBeVisible();
  await expect(page.getByText("Echo: first question")).toHaveCount(0);
  await expect(page.getByText("· edited")).toBeVisible();
});

test("rename, pin, archive, search and delete conversations", async ({ page }) => {
  await sendMessage(page, "zebra facts please");
  await expect(page.getByText("Echo: zebra facts please")).toBeVisible();
  await openSidebar(page);
  await page.getByRole("button", { name: /More actions for zebra facts/ }).click();
  await page.getByRole("menuitem", { name: "Rename" }).click();
  await page.getByLabel("New title").fill("Zebra notes");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("button", { name: /^Zebra notes/ })).toBeVisible();

  await page.getByRole("button", { name: /More actions for Zebra notes/ }).click();
  await page.getByRole("menuitem", { name: "Pin to top" }).click();
  await expect(page.getByRole("button", { name: /^Zebra notes.*pinned/ })).toBeVisible();

  await page.getByLabel("Search conversations").fill("zebra");
  await expect(page.getByRole("button", { name: /^Zebra notes/ })).toBeVisible();
  await page.getByLabel("Search conversations").fill("nothing-matches-this");
  await expect(page.getByText(/No chats match/)).toBeVisible();
  await page.getByLabel("Search conversations").fill("");

  await page.getByRole("button", { name: /More actions for Zebra notes/ }).click();
  await page.getByRole("menuitem", { name: "Archive" }).click();
  await expect(page.getByRole("button", { name: /^Zebra notes/ })).toHaveCount(0);
  await page.getByRole("tab", { name: "Archived" }).click();
  await expect(page.getByRole("button", { name: /^Zebra notes/ })).toBeVisible();

  await page.getByRole("button", { name: /More actions for Zebra notes/ }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("No archived chats.")).toBeVisible();
});

test("export a conversation as Markdown", async ({ page }) => {
  await sendMessage(page, "export me");
  await expect(page.getByText("Echo: export me")).toBeVisible();
  await openSidebar(page);
  await page.getByRole("button", { name: /More actions for export me/ }).click();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("menuitem", { name: "Export as Markdown" }).click()]);
  expect(download.suggestedFilename()).toMatch(/\.md$/);
});

test("stop keeps the partial reply", async ({ page }) => {
  // word-by-word streaming happens with agent tools off (with tools on, replies arrive in one piece)
  await page.getByRole("button", { name: /tools on/ }).click();
  await page.getByRole("switch", { name: "Use agent tools" }).click();
  await page.keyboard.press("Escape");
  await sendMessage(page, "slow reply please with several words here");
  await expect(page.getByText(/^Echo:/)).toBeVisible();
  await page.getByRole("button", { name: "Stop generating" }).click();
  await expect(page.getByRole("button", { name: "Send message" })).toBeVisible();
  await expect(page.getByText(/^Echo:/)).toBeVisible();
});

test("provider errors are shown with a retry button", async ({ page }) => {
  await sendMessage(page, "fail500 please");
  await expect(page.getByRole("alert").filter({ hasText: "AI provider returned an error" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
});

test("thumbs up is saved", async ({ page }) => {
  await sendMessage(page, "rate this");
  await expect(page.getByText("Echo: rate this")).toBeVisible();
  const up = page.getByRole("button", { name: "Good response" });
  await up.click();
  await expect(up).toHaveAttribute("aria-pressed", "true");
  await page.reload();
  await openSidebar(page);
  await page.getByRole("button", { name: /^rate this/ }).click();
  await expect(page.getByRole("button", { name: "Good response" })).toHaveAttribute("aria-pressed", "true");
});
