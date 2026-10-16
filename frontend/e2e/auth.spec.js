import { expect, test } from "@playwright/test";
import { PASSWORD, login, openSidebar, register, sendMessage, uniqueEmail } from "./helpers";

test("register, chat, sign out and sign back in to the same history", async ({ page }, info) => {
  const email = uniqueEmail(info);
  await register(page, email);
  await sendMessage(page, "remember this conversation");
  await expect(page.getByText("Echo: remember this conversation")).toBeVisible();

  // the session lives in an HttpOnly cookie, not in storage readable by scripts
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + document.cookie);
  expect(stored).not.toContain("nova_session");

  await openSidebar(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();

  await login(page, email);
  await openSidebar(page);
  await page.getByRole("button", { name: /remember this conversation/ }).first().click();
  await expect(page.getByText("Echo: remember this conversation")).toBeVisible();
});

test("wrong password shows an error and no session", async ({ page }, info) => {
  const email = uniqueEmail(info);
  await register(page, email);
  await openSidebar(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await login(page, email, "not-the-password");
  await expect(page.getByRole("alert")).toContainText("Incorrect email or password");
  await expect(page.locator("#chat-input")).toHaveCount(0);
});

test("form validation is shown inline", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Enter a valid email address.")).toBeVisible();
  await page.getByRole("button", { name: "Create an account" }).click();
  await page.getByLabel("Password", { exact: true }).fill("short");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("Use at least 8 characters.", { exact: true })).toBeVisible();
});

test("session survives a reload; signing out on one device ends it there only", async ({ browser }, info) => {
  const email = uniqueEmail(info);
  const a = await (await browser.newContext()).newPage();
  await register(a, email);
  const b = await (await browser.newContext()).newPage();
  await login(b, email);
  await expect(b.locator("#chat-input")).toBeVisible();
  await a.reload();
  await expect(a.locator("#chat-input")).toBeVisible();
  await openSidebar(b);
  await b.getByRole("button", { name: "Sign out" }).click();
  await a.reload();
  await expect(a.locator("#chat-input")).toBeVisible();
  expect(PASSWORD).toBeTruthy();
});
