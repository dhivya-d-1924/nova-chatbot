import { expect, test } from "@playwright/test";
import { login, openSidebar, register, sendMessage, uniqueEmail } from "./helpers";

test.describe.configure({ mode: "serial" });

async function asAdmin(page) {
  // the admin account is created once by global-setup.js (ADMIN_EMAILS on the e2e server)
  await login(page, "admin@e2e.test");
  await expect(page.locator("#chat-input")).toBeVisible();
}

test("admin dashboard: users, support reply, usage and audit log", async ({ browser }, info) => {
  // a normal user asks for a human
  const userPage = await (await browser.newContext()).newPage();
  const email = uniqueEmail(info, "member");
  await register(userPage, email, "Member");
  await sendMessage(userPage, "hello admin");
  await expect(userPage.getByText("Echo: hello admin")).toBeVisible();
  await userPage.getByRole("button", { name: /tools on/ }).click();
  await userPage.getByLabel(/Talk to a human/).fill("Please call me about billing");
  await userPage.getByRole("button", { name: "Send request" }).click();
  await expect(userPage.getByText(/A person will reply in this chat/)).toBeVisible();

  const page = await (await browser.newContext()).newPage();
  await asAdmin(page);
  await openSidebar(page);
  await page.getByRole("button", { name: "Admin dashboard" }).click();
  await expect(page.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("Configuration")).toBeVisible();

  await page.getByRole("tab", { name: "Users" }).click();
  await page.getByLabel("Search users").fill(email.slice(0, 20));
  await expect(page.getByText(email)).toBeVisible();

  await page.getByRole("tab", { name: "Support" }).click();
  const req = page.getByRole("listitem").filter({ hasText: "Please call me about billing" }).filter({ hasText: email });
  await req.getByRole("textbox").fill("We'll call you today.");
  await req.getByRole("button", { name: "Reply & resolve" }).click();
  await expect(page.getByText(/Reply sent to the user's chat/)).toBeVisible();

  await page.getByRole("tab", { name: "Usage" }).click();
  await expect(page.getByText("AI requests")).toBeVisible();
  await page.getByRole("tab", { name: "Audit log" }).click();
  await expect(page.getByRole("tabpanel").getByRole("listitem").filter({ hasText: "user.register" }).first()).toBeVisible();

  // the reply reached the user's chat
  await userPage.reload();
  await openSidebar(userPage);
  await userPage.getByRole("button", { name: /^hello admin/ }).click();
  await expect(userPage.getByText("We'll call you today.")).toBeVisible();
});

test("non-admins do not see the dashboard", async ({ page }, info) => {
  await register(page, uniqueEmail(info));
  await openSidebar(page);
  await expect(page.getByRole("button", { name: "Admin dashboard" })).toHaveCount(0);
  const r = await page.request.get("/api/admin/stats");
  expect(r.status()).toBe(403);
});
