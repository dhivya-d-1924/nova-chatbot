import { expect, test } from "@playwright/test";
import { register, sendMessage, uniqueEmail } from "./helpers";

test.beforeEach(async ({ page }, info) => { await register(page, uniqueEmail(info)); });

test("agent uses the calculator and shows the step", async ({ page }) => {
  await sendMessage(page, "calc 48213*9127");
  await expect(page.getByText("Calculating 48213*9127")).toBeVisible();
  await expect(page.getByText(/Tool result:.*440040051/)).toBeVisible();
});

test("sending an email waits for approval and runs only after Approve", async ({ page }) => {
  await sendMessage(page, "email bob@example.com about lunch");
  const card = page.getByRole("group", { name: "Approval needed" });
  await expect(card).toBeVisible();
  await expect(card).toContainText("bob@example.com");
  await card.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/NOT delivered/).first()).toBeVisible();
  await expect(page.getByRole("group", { name: "Approval needed" })).toHaveCount(0);
});

test("declining an email never sends it", async ({ page }) => {
  await sendMessage(page, "email eve@example.com now");
  await page.getByRole("group", { name: "Approval needed" }).getByRole("button", { name: "Decline" }).click();
  await expect(page.getByText("You declined").first()).toBeVisible();
});

test("tools can be switched off from the assistant menu", async ({ page }) => {
  await page.getByRole("button", { name: /tools on/ }).click();
  await page.getByRole("switch", { name: "Use agent tools" }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: /tools off/ })).toBeVisible();
  await sendMessage(page, "calc 2+2");
  await expect(page.getByText("Echo: calc 2+2")).toBeVisible();
});
