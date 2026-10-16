import crypto from "node:crypto";
import { expect } from "@playwright/test";

export const PASSWORD = "E2e-Strong-Pass-42";
export const uniqueEmail = (testInfo, tag = "u") =>
  `${tag}-${testInfo.project.name}-${testInfo.workerIndex}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@e2e.test`;

export async function register(page, email, name = "E2E User") {
  await page.goto("/");
  await page.getByRole("button", { name: "Create an account" }).click();
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.locator("#chat-input")).toBeVisible();
}

export async function login(page, email, password = PASSWORD) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

/** Open the conversation drawer on small screens; no-op on desktop where the sidebar is always visible. */
export async function openSidebar(page) {
  if (page.viewportSize().width >= 1024) return;
  const menu = page.getByRole("button", { name: "Open menu" });
  await expect(menu).toBeVisible(); // waits for the app to finish loading after a reload or sign-in
  await menu.click();
  await expect(page.getByRole("dialog", { name: "Conversations" })).toBeVisible();
  // wait for the slide-in to finish: a tap during the animation lands on the backdrop and closes the drawer
  await page.waitForFunction(() => {
    const d = document.querySelector('[role="dialog"][aria-label="Conversations"]');
    return d && Math.abs(d.getBoundingClientRect().left) < 0.5;
  });
  await page.waitForTimeout(150);
}

export async function sendMessage(page, text) {
  await page.locator("#chat-input").fill(text);
  await page.getByRole("button", { name: "Send message" }).click();
}

export async function noHorizontalOverflow(page) {
  const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  expect(sw, "page must not scroll horizontally").toBeLessThanOrEqual(cw);
}

// RFC 6238 TOTP, matching the server's implementation
function base32(s) {
  const a = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of s.replace(/[=\s]/g, "").toUpperCase()) bits += a.indexOf(c).toString(2).padStart(5, "0");
  const out = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(out);
}
export function totp(secret, offsetSteps = 0) {
  const counter = Math.floor(Date.now() / 30000) + offsetSteps;
  const buf = Buffer.alloc(8);
  buf.writeBigUInt64BE(BigInt(counter));
  const h = crypto.createHmac("sha1", base32(secret)).update(buf).digest();
  const o = h[h.length - 1] & 15;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1e6).padStart(6, "0");
}
