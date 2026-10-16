import { expect, test } from "@playwright/test";
import { PASSWORD, login, openSidebar, register, totp, uniqueEmail } from "./helpers";

async function openAccount(page) {
  await openSidebar(page);
  await page.getByRole("button", { name: "Account" }).click();
  return page.getByRole("dialog", { name: "Account" });
}

test("set up two-step verification and sign in with a code", async ({ page }, info) => {
  const email = uniqueEmail(info);
  await register(page, email);
  const dlg = await openAccount(page);
  await dlg.getByRole("tab", { name: "Security" }).click();
  await dlg.getByRole("button", { name: "Set up" }).click();
  await dlg.getByLabel("Confirm your password").fill(PASSWORD);
  await dlg.getByRole("button", { name: "Continue" }).click();
  const key = (await dlg.locator("code").innerText()).replace(/\s/g, "");
  await dlg.getByLabel(/Enter the 6-digit code/).fill(totp(key));
  await dlg.getByRole("button", { name: "Turn on" }).click();
  await expect(dlg.getByText("Save your recovery codes")).toBeVisible();
  await dlg.getByRole("button", { name: "I've saved them" }).click();
  await expect(dlg.getByText("On", { exact: true })).toBeVisible();
  await dlg.getByRole("button", { name: "Close" }).click();

  await openSidebar(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, email);
  await expect(page.getByRole("heading", { name: "Two-step verification" })).toBeVisible();
  await page.getByLabel("Verification code").fill(totp(key, 1)); // next time step: the enrolment code can't be replayed
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.locator("#chat-input")).toBeVisible();
});

test("theme preference follows the user to another device", async ({ browser }, info) => {
  const email = uniqueEmail(info);
  const laptop = await (await browser.newContext()).newPage();
  await register(laptop, email);
  const dlg = await openAccount(laptop);
  await dlg.getByRole("radio", { name: "Light" }).click();
  await expect(laptop.locator("html")).toHaveClass(/light/);
  await expect(laptop.getByText(/Couldn't save/)).toHaveCount(0);

  const phone = await (await browser.newContext()).newPage();
  await login(phone, email);
  await expect(phone.locator("#chat-input")).toBeVisible();
  await expect(phone.locator("html")).toHaveClass(/light/);
});

test("change password and see signed-in devices", async ({ page }, info) => {
  const email = uniqueEmail(info);
  await register(page, email);
  const dlg = await openAccount(page);
  await dlg.getByRole("tab", { name: "Security" }).click();
  await dlg.getByLabel("Current password").fill(PASSWORD);
  await dlg.getByLabel("New password").fill("Another-Strong-Pass-7");
  await dlg.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByText(/Password changed/)).toBeVisible();
  await dlg.getByRole("tab", { name: "Devices" }).click();
  await expect(dlg.getByText("This device")).toBeVisible();
});

test("delete account removes access", async ({ page }, info) => {
  const email = uniqueEmail(info);
  await register(page, email);
  const dlg = await openAccount(page);
  await dlg.getByRole("tab", { name: "Your data" }).click();
  const [download] = await Promise.all([page.waitForEvent("download"), dlg.getByRole("button", { name: "Download" }).click()]);
  expect(download.suggestedFilename()).toBe("nova-account-export.json");
  await dlg.getByLabel("Your password").fill(PASSWORD);
  await dlg.getByLabel("Type DELETE to confirm").fill("DELETE");
  await dlg.getByRole("button", { name: "Delete my account" }).click();
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await login(page, email);
  await expect(page.getByRole("alert")).toContainText("Incorrect email or password");
});
