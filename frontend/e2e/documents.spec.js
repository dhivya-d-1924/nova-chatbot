import { expect, test } from "@playwright/test";
import { register, sendMessage, uniqueEmail } from "./helpers";

test("upload a document, ask about it and open the cited passage", async ({ page }, info) => {
  await register(page, uniqueEmail(info));
  await page.getByRole("button", { name: "Documents", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Your documents" });
  await dialog.locator("input[type=file]").setInputFiles({ name: "policy.txt", mimeType: "text/plain",
    buffer: Buffer.from("Returns are accepted within 30 days of delivery with a receipt.") });
  await expect(dialog.getByText("policy.txt", { exact: true })).toBeVisible();
  await expect(page.getByText(/is ready/)).toBeVisible();
  // duplicate upload is refused with a clear message
  await dialog.locator("input[type=file]").setInputFiles({ name: "copy.txt", mimeType: "text/plain",
    buffer: Buffer.from("Returns are accepted within 30 days of delivery with a receipt.") });
  await expect(dialog.getByText(/already uploaded/)).toBeVisible();
  await dialog.getByRole("button", { name: "Close" }).click();

  await expect(page.getByRole("button", { name: /Answering from 1 document/ })).toBeVisible();
  await sendMessage(page, "How many days for returns?");
  const chip = page.getByRole("button", { name: /policy\.txt/ });
  await expect(chip).toBeVisible();
  await chip.click();
  await expect(page.getByText("Returns are accepted within 30 days").last()).toBeVisible();
});

test("unsupported files are rejected before upload", async ({ page }, info) => {
  await register(page, uniqueEmail(info));
  await page.getByRole("button", { name: "Documents", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Your documents" });
  await dialog.locator("input[type=file]").setInputFiles({ name: "run.exe", mimeType: "application/octet-stream", buffer: Buffer.from("MZ") });
  await expect(dialog.getByText(/Unsupported type/)).toBeVisible();
});
