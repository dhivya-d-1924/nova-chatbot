import { expect, test } from "@playwright/test";
import { noHorizontalOverflow, openSidebar, register, sendMessage, uniqueEmail } from "./helpers";

/** Connect a simulated account the way Google/Microsoft would: follow the callback with the state Nova issued. */
async function connect(page, path, callback) {
  const url = await page.evaluate(async (p) => {
    const r = await fetch(`/api/${p}`, { credentials: "include" });
    return (await r.json()).url;
  }, path);
  const state = new URL(url).searchParams.get("state");
  expect(new URL(url).searchParams.get("code_challenge_method")).toBe("S256");
  await page.goto(`/api/${callback}?code=e2e&state=${encodeURIComponent(state)}`);
}

test("workspace: connect accounts, unified day view, schedule with approval, leave, tasks and automation", async ({ page }, info) => {
  await register(page, uniqueEmail(info, "ws"));
  await connect(page, "integrations/google/connect", "integrations/google/callback");
  await expect(page.getByText(/Google connected/)).toBeVisible();
  await expect(page.getByRole("main", { name: "Workspace" })).toBeVisible();
  await connect(page, "workspace/microsoft/connect", "workspace/microsoft/callback");
  await expect(page.getByText(/Microsoft 365 connected/)).toBeVisible();
  const ws = page.getByRole("main", { name: "Workspace" });
  await expect(ws.getByText("Connected as").first()).toBeVisible();
  await noHorizontalOverflow(page);

  // today: meetings from both calendars, with the conflict flagged
  await ws.getByRole("tab", { name: "Today" }).click();
  const meetings = ws.getByRole("list", { name: "Meetings" });
  await expect(meetings.getByText("Design review").first()).toBeVisible();
  await expect(meetings.getByText("Client call").first()).toBeVisible();
  await expect(ws.getByText(/Conflict:/).first()).toBeVisible();
  await noHorizontalOverflow(page);

  // find a time and prepare an invitation, which waits in Approvals
  await ws.getByLabel("Attendees").fill("priya@e2e.test");
  await ws.getByRole("button", { name: "Find times" }).click();
  await ws.getByRole("radiogroup", { name: "Free times" }).getByRole("radio").first().click();
  await ws.getByLabel("Meeting title").fill("Sprint planning");
  await ws.getByRole("button", { name: "Prepare invitation" }).click();
  await ws.getByRole("tab", { name: /Approvals/ }).click();
  const item = ws.getByRole("list", { name: "Prepared actions" }).getByRole("listitem").first();
  await expect(item.getByText("Create “Sprint planning”")).toBeVisible();
  await expect(item.getByText("priya@e2e.test")).toBeVisible();
  await item.getByRole("button", { name: "Approve and send" }).click();
  await expect(page.getByText(/confirmed by (google|microsoft)/).first()).toBeVisible();

  // leave: scan finds Priya's message as a proposal, confirm it
  await ws.getByRole("tab", { name: "Leave" }).click();
  await ws.getByRole("button", { name: "Scan messages" }).click();
  await expect(ws.getByText("Needs your check", { exact: true })).toBeVisible();
  await ws.getByRole("button", { name: "Confirm" }).click();
  await expect(ws.getByText("Needs your check", { exact: true })).toHaveCount(0);
  await noHorizontalOverflow(page);

  // tasks
  await ws.getByRole("tab", { name: "Tasks" }).click();
  await ws.getByLabel("Action item", { exact: true }).fill("Send the release notes");
  await ws.getByRole("form", { name: "Add an action item" }).getByRole("button", { name: "Add" }).click();
  await expect(ws.getByRole("list", { name: "Action items" }).getByText("Send the release notes")).toBeVisible();

  // automation: assisted by default; pausing is one click
  await ws.getByRole("tab", { name: "Automation" }).click();
  await expect(ws.getByRole("radio", { name: /Assisted/ })).toBeChecked();
  await ws.getByRole("button", { name: "Pause all automation" }).click();
  await expect(ws.getByText(/Paused: nothing runs on its own/)).toBeVisible();
  await noHorizontalOverflow(page);

  // activity shows the confirmed meeting
  await ws.getByRole("tab", { name: "Activity" }).click();
  await expect(ws.getByRole("list", { name: "Activity" }).getByText(/Meeting created and confirmed/)).toBeVisible();

  // the assistant can read the calendar in chat
  await page.getByRole("button", { name: /^(Back to chat|Chat)$/ }).click();
  await sendMessage(page, "app calendar_events {}");
  await expect(page.getByText(/Tool result:/).last()).toBeVisible();
  await openSidebar(page);
  await expect(page.getByRole("button", { name: "Workspace" })).toBeVisible();
});
