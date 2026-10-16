import { expect, test } from "@playwright/test";
import { noHorizontalOverflow, openSidebar, register, sendMessage, uniqueEmail } from "./helpers";

test("every main screen fits the viewport without horizontal scrolling", async ({ page }, info) => {
  await page.goto("/");
  await noHorizontalOverflow(page);
  await register(page, uniqueEmail(info));
  await noHorizontalOverflow(page);
  await sendMessage(page, "A long message with a URL https://example.com/a/really/long/path/that/should/wrap/nicely/in/the/bubble/without/overflow and a table | a | b |");
  await expect(page.getByText(/^Echo:/)).toBeVisible();
  await noHorizontalOverflow(page);
  await openSidebar(page);
  await noHorizontalOverflow(page);
  await page.getByRole("button", { name: "Account" }).click();
  await expect(page.getByRole("dialog", { name: "Account" })).toBeVisible();
  await noHorizontalOverflow(page);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Account" })).toHaveCount(0);
  await page.getByRole("button", { name: "Documents", exact: true }).click();
  await noHorizontalOverflow(page);
  await page.keyboard.press("Escape");
});

test("touch targets in the header and composer are at least 40px", async ({ page }, info) => {
  test.skip(!info.project.use.hasTouch, "touch devices only");
  await register(page, uniqueEmail(info));
  const sizes = await page.evaluate(() =>
    [...document.querySelectorAll("header button, #chat-input ~ button, main button[aria-label='Send message']")]
      .filter((b) => b.offsetParent !== null)
      .map((b) => ({ name: b.getAttribute("aria-label") || b.textContent, w: b.getBoundingClientRect().width, h: b.getBoundingClientRect().height })));
  expect(sizes.length).toBeGreaterThan(2);
  for (const s of sizes) expect(Math.min(s.w, s.h), s.name).toBeGreaterThanOrEqual(40);
});

test("inputs use 16px text on phones so iOS doesn't zoom", async ({ page }, info) => {
  test.skip(info.project.use.viewport.width >= 640, "phones only");
  await page.goto("/");
  const size = await page.getByLabel("Email").evaluate((el) => getComputedStyle(el).fontSize);
  expect(parseFloat(size)).toBeGreaterThanOrEqual(16);
  await register(page, uniqueEmail(info));
  expect(parseFloat(await page.locator("#chat-input").evaluate((el) => getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(16);
});

test("drawer and dialogs are keyboard accessible", async ({ page }, info) => {
  await register(page, uniqueEmail(info));
  await page.getByRole("button", { name: "Documents", exact: true }).click();
  const dlg = page.getByRole("dialog", { name: "Your documents" });
  await expect(dlg).toBeVisible();
  for (let i = 0; i < 8; i += 1) await page.keyboard.press("Tab"); // focus stays trapped inside the dialog
  expect(await dlg.evaluate((d) => d.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dlg).toHaveCount(0);
  const menu = page.getByRole("button", { name: "Open menu" });
  if (await menu.isVisible()) {
    await menu.click();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Conversations" })).toHaveCount(0);
  }
});

test("every button has an accessible name", async ({ page }, info) => {
  await register(page, uniqueEmail(info));
  await sendMessage(page, "check names");
  await expect(page.getByText("Echo: check names")).toBeVisible();
  const unnamed = await page.evaluate(() => [...document.querySelectorAll("button")]
    .filter((b) => b.offsetParent !== null && !(b.getAttribute("aria-label") || b.textContent.trim() || b.getAttribute("title")))
    .map((b) => b.outerHTML.slice(0, 120)));
  expect(unnamed).toEqual([]);
});

test("offline banner appears and clears", async ({ page, context }, info) => {
  await register(page, uniqueEmail(info));
  await context.setOffline(true);
  await page.evaluate(() => window.dispatchEvent(new Event("offline")));
  await expect(page.getByText(/You're offline/).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Send message" })).toBeDisabled();
  await context.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.getByText(/You're offline/)).toHaveCount(0);
});
