import { expect, test } from "@playwright/test";
import { noHorizontalOverflow, register, uniqueEmail } from "./helpers";

async function connect(page, path, callback) {
  const url = await page.evaluate(async (p) => (await (await fetch(`/api/${p}`, { credentials: "include" })).json()).url, path);
  const state = new URL(url).searchParams.get("state");
  await page.goto(`/api/${callback}?code=e2e&state=${encodeURIComponent(state)}`);
}

test("inbox: needs a response across Gmail and Teams, email to Teams update with approval, action items, triage, filtered search", async ({ page }, info) => {
  await register(page, uniqueEmail(info, "inbox"));
  await connect(page, "integrations/google/connect", "integrations/google/callback");
  await connect(page, "workspace/microsoft/connect", "workspace/microsoft/callback");
  const ws = page.getByRole("main", { name: "Workspace" });
  await expect(ws.getByLabel("Connections")).toContainText("Gmail & Google Calendar: connected");
  await expect(ws.getByLabel("Connections")).toContainText("Teams & Outlook: connected");

  // one list for both platforms, each item labelled with its source
  await ws.getByRole("tab", { name: "Inbox" }).click();
  const waiting = ws.getByRole("list", { name: "Waiting for a response" });
  const urgent = waiting.getByRole("listitem").filter({ hasText: "URGENT: production checkout errors" });
  await expect(urgent.getByText("Gmail", { exact: true })).toBeVisible();
  await expect(waiting.getByRole("listitem").filter({ hasText: "Project X" }).getByText("Teams", { exact: true })).toBeVisible();
  await noHorizontalOverflow(page);

  // turn the urgent email into a Teams channel update: it becomes a draft, sent only after approval
  await urgent.getByRole("button", { name: "Draft a response" }).click();
  const form = urgent.getByRole("form", { name: "Prepare a draft" });
  await form.getByLabel("Draft").selectOption("teams_channel");
  await form.getByLabel("Channel").selectOption({ label: "Engineering › General" });
  await form.getByRole("button", { name: "Write draft" }).click();
  await expect(page.getByText(/Draft ready in Approvals/)).toBeVisible();
  await ws.getByRole("tab", { name: /Approvals/ }).click();
  const item = ws.getByRole("list", { name: "Prepared actions" }).getByRole("listitem").filter({ hasText: "Teams channel update from email" }).first();
  await expect(item.getByLabel("What will happen")).toContainText("production checkout errors");
  await item.getByRole("button", { name: "Approve and send" }).click();
  await expect(page.getByText("Posted in the Teams channel.").first()).toBeVisible();

  // action items from both platforms, with their source
  await ws.getByRole("tab", { name: "Inbox" }).click();
  await ws.getByLabel("Look back").selectOption("7");
  await ws.getByRole("button", { name: "Find action items" }).click();
  const found = ws.getByRole("list", { name: "New action items" });
  await expect(found.getByText(/rollback plan/i)).toBeVisible();
  await noHorizontalOverflow(page);

  // email triage
  await ws.getByRole("button", { name: "Sort now" }).click();
  await expect(ws.getByRole("region", { name: "Urgent" })).toContainText("URGENT: production checkout errors");
  await expect(ws.getByRole("region", { name: "Low priority" })).toContainText("This week in tech");
  await noHorizontalOverflow(page);

  // filtered search on Today
  await ws.getByRole("tab", { name: "Today" }).click();
  await ws.getByRole("button", { name: /Filters/ }).click();
  await ws.getByLabel("Source").selectOption("teams");
  await ws.getByLabel("Search Gmail and Teams").fill("payment");
  await ws.getByRole("button", { name: "Search", exact: true }).click();
  await expect(ws.getByRole("list", { name: "Search results" }).getByText("Teams channel").first()).toBeVisible();
  await noHorizontalOverflow(page);
});
