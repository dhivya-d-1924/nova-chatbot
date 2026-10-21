import { expect, test } from "@playwright/test";
import { noHorizontalOverflow, openSidebar, register, sendMessage, uniqueEmail } from "./helpers";

const SYLLABUS = "Biology syllabus. Unit 1: Photosynthesis and chlorophyll. Unit 2: Respiration. Unit 3: Genetics.";
const MATERIAL = "Photosynthesis happens in chloroplasts. Chlorophyll absorbs light energy so plants make glucose.";

test("study mode: subject, material, 5-day plan, restricted tutor chat and open doubts", async ({ page }, info) => {
  await register(page, uniqueEmail(info));
  await openSidebar(page);
  await page.getByRole("button", { name: "Study", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Study" });
  await dialog.getByLabel("Subject name").fill("Biology");
  await dialog.getByRole("button", { name: "Create subject" }).click();
  await expect(dialog.getByRole("heading", { name: "Biology" })).toBeVisible();

  // material: syllabus + study material
  await dialog.getByRole("radio", { name: "Syllabus" }).check();
  await dialog.locator("#study-upload").setInputFiles({ name: "syllabus.txt", mimeType: "text/plain", buffer: Buffer.from(SYLLABUS) });
  await expect(dialog.getByText("syllabus.txt")).toBeVisible();
  await dialog.getByRole("radio", { name: "Study material" }).check();
  await dialog.locator("#study-upload").setInputFiles({ name: "chapter1.txt", mimeType: "text/plain", buffer: Buffer.from(MATERIAL) });
  await expect(dialog.getByText("chapter1.txt")).toBeVisible();
  await noHorizontalOverflow(page);

  // 5-day plan
  await dialog.getByRole("tab", { name: /Plan/ }).click();
  await dialog.getByRole("button", { name: "Make 5-day plan" }).click();
  await expect(dialog.getByText("Day 5: Part 5")).toBeVisible();
  await dialog.getByRole("button", { name: "Mark day 1 done" }).click();
  await expect(dialog.getByText("1 of 5 days done")).toBeVisible();

  // study material for a topic
  await dialog.getByRole("tab", { name: /Notes/ }).click();
  await dialog.getByLabel("Make study material").fill("photosynthesis chlorophyll");
  await dialog.getByRole("button", { name: "Make notes" }).click();
  await expect(dialog.getByRole("heading", { name: "Overview" })).toBeVisible();
  await dialog.getByRole("button", { name: "All notes" }).click();

  // restricted tutor chat
  await dialog.getByRole("button", { name: "Ask the Biology tutor" }).click();
  await expect(page.getByText("Study mode · Biology")).toBeVisible();
  await sendMessage(page, "How does chlorophyll absorb light energy?");
  await expect(page.getByText("chlorophyll absorbs light").last()).toBeVisible();
  await sendMessage(page, "Who won the football world cup final in the year 2022 in Qatar?");
  await expect(page.getByText(/couldn't find this in your Biology material/)).toBeVisible();

  // the question is in the open doubts index; resolve it
  await page.getByRole("button", { name: "Open Biology in Study" }).click();
  await dialog.getByRole("tab", { name: /Doubts/ }).click();
  await expect(dialog.getByText("Who won the football world cup final in the year 2022 in Qatar?")).toBeVisible();
  await dialog.getByLabel("Answer or note").first().fill("Not part of the syllabus.");
  await dialog.getByRole("button", { name: "Resolve" }).first().click();
  await expect(dialog.getByText("No open doubts. 🎉")).toBeVisible();
});
