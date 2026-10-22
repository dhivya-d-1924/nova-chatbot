# Workspace agent: Gmail, Microsoft Teams, calendars and leave

Nova's **Workspace** connects to each person's own Google Workspace and/or Microsoft 365 account. It shows one view of
their day, prepares emails, Teams messages and meeting invitations, tracks leave, sets out-of-office replies, keeps
action items and reminders, and runs scheduled briefings. **Nothing anyone else can see is sent without the person's
approval** unless they deliberately pick a narrower automation mode.

Contents: [What it does](#what-it-does) · [Try it without accounts](#try-it-without-real-accounts-demo-mode) ·
[Google setup](#1-google-workspace-setup) · [Microsoft 365 setup](#2-microsoft-365-setup) ·
[Automation modes](#3-automation-modes) · [Background jobs](#4-background-jobs) · [Security](#5-security) ·
[Testing](#6-what-is-tested-and-how) · [Limits](#7-known-limits) · [Troubleshooting](#8-troubleshooting) ·
[API](#9-api) · [Database](#10-database-schema-and-retention)

---

## What it does

| Area | In the Workspace screen | Ask in chat, for example |
|---|---|---|
| **Today** | Meetings from Google and Outlook in one list, conflicts flagged, Join links | "What meetings do I have tomorrow?" |
| **Inbox** | **Needs a response** across Gmail and Teams in one list (source, sender, link); **Draft a response**: an AI-written email reply, a Teams update or channel post from an email, or an email from a Teams discussion, which waits in Approvals; **Find action items** in recent emails and Teams chats (owner, deadline, source link; never added twice); **Sort my email** into urgent / needs a reply / FYI / low priority; mark read/unread | "Summarise today's important Gmail emails and Teams messages", "Prepare a Teams update from this email", "Draft an email to the project manager about the production issue discussed in Teams", "Show emails and Teams messages that need a response", "Find all pending action items and their deadlines" |
| **Search** | One query across Gmail and Teams (chats and readable channels), filtered by source, sender and dates; "More emails" pages through Gmail | "Find the latest discussion about the deployment issue" |
| **Briefing** | Today / tomorrow / since yesterday / this week / last week; facts kept separate from suggestions | "Brief me on today" |
| **Find a time** | Free slots across your and colleagues' calendars, inside your working hours and days, skipping holidays | "Find 30 minutes with priya@… next week" |
| **Meetings** | Prepare an invitation (Google Meet or Teams link); reschedule; cancel (or decline if you're not the organiser) | "Move my 3pm design review to Thursday" |
| **Email** | Unanswered threads, full text with attachment list, drafts, labels, mark read, reply in thread, forward **with attachments** (up to 20 MB, listed on the approval card), send | "Which emails from clients haven't I answered?" |
| **Teams** | Recent chats, chats waiting for you (questions flagged), read a chat or channel, search by words, sender, dates or channel, post to a chat or channel | "What did Arun say about the release in Teams?" |
| **Approvals** | Every prepared action with a preview of recipients, text, attachments and destination; edit, approve once, or discard. Failed sends show the reason with **Retry** (never sends twice) or **Cancel it** | – |
| **Leave** | Absences list; scan Gmail and Teams for leave messages (proposed, you confirm); meetings affected by an absence; draft reschedule notices with three slots after the person returns (no reason given) | "Priya is on leave Monday to Wednesday, what's affected?" |
| **Out of office** | Internal and external messages, alternate contact, preview, send a test to yourself, turn on for Gmail and/or Outlook with start and end | "Set my out-of-office for 3–7 November" |
| **Tasks** | Action items with owner and due date; reminders; "follow up if no reply by Friday" | "Remind me to follow up if Ravi doesn't reply by Friday" |
| **Automation** | Mode, pause, allowed automatic actions, routines, time zone, working hours/days, holidays, quiet hours, notification channels, scheduled jobs | – |
| **Activity** | Everything done for you, with the provider's confirmation link | – |
| **Accounts** | Connect, reconnect or disconnect Google and Microsoft; which admin-only permissions are on. Connection status is shown at the top of every Workspace tab | – |

Notifications appear under the bell, and optionally by email, WhatsApp or Teams (those need the person's apps linked in
**Account → Apps**). During quiet hours only urgent notifications go out on those channels; the rest wait.

### Try it without real accounts (demo mode)
For development and demos, set `WORKSPACE_DEMO=true` (with `APP_ENV=development` or `testing`) and restart. **Connect**
then links simulated Gmail, Google Calendar, Outlook Calendar and Teams accounts with a believable workday: meetings
with a clash, a leave note, an urgent email with a log file attached, a newsletter, a Teams question and a channel
discussion. Every feature works against them (AI features use your configured AI provider). A banner says "Demo mode",
nothing reaches real people, and production refuses to start with it on. The same simulated providers run the
automated tests.

---

## 1. Google Workspace setup

The Google part uses the same OAuth client as [INTEGRATIONS.md](INTEGRATIONS.md); if you already set that up, add
the new scopes below and ask people to click **Reconnect**.

1. **console.cloud.google.com** → your project → **APIs & Services → Library**: enable **Google Calendar API**,
   **Gmail API** and **Google Drive API**.
2. **OAuth consent screen**: User type **Internal**. Scopes:
   - `openid`, `email`
   - `https://www.googleapis.com/auth/calendar.events`
   - `https://www.googleapis.com/auth/calendar.freebusy`
   - `https://www.googleapis.com/auth/gmail.modify` (read, label, draft, send; sending always asks first)
   - `https://www.googleapis.com/auth/gmail.settings.basic` (out-of-office)
   - `https://www.googleapis.com/auth/drive.readonly`
3. **Credentials → OAuth client ID → Web application**, redirect URI
   `https://<your-nova-address>/api/integrations/google/callback`.
4. Server settings:
   ```env
   GOOGLE_CLIENT_ID=…apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=…
   GOOGLE_ALLOWED_DOMAIN=yourcompany.com
   ```
   Colleagues' free/busy is only visible if your Workspace calendar sharing allows it (Admin console → Apps → Google
   Workspace → Calendar → Sharing settings). Otherwise those colleagues are listed as "availability unknown".

## 2. Microsoft 365 setup

1. **entra.microsoft.com → Applications → App registrations → New registration**
   - Name: Nova
   - Supported account types: **Accounts in this organizational directory only**
   - Redirect URI (Web): `https://<your-nova-address>/api/workspace/microsoft/callback`
2. **Certificates & secrets → New client secret**; copy the *Value* (shown once).
3. **API permissions → Add → Microsoft Graph → Delegated permissions**:

   | Permission | Used for | Admin consent |
   |---|---|---|
   | `openid`, `profile`, `email`, `offline_access`, `User.Read` | Sign-in, staying connected | No |
   | `User.ReadBasic.All` | Finding colleagues by name | Usually no* |
   | `Calendars.ReadWrite` | Outlook Calendar, Teams meeting links, colleagues' availability | No |
   | `MailboxSettings.ReadWrite` | Outlook automatic replies | No |
   | `Chat.ReadWrite` | Read, search and send Teams chats | No* |
   | `ChannelMessage.Send`, `Team.ReadBasic.All`, `Channel.ReadBasic.All` | Post to channels you choose | No* |
   | `ChannelMessage.Read.All` (optional) | Reading channel messages | **Yes** |
   | `Presence.Read.All` (optional) | Reserved; not used yet | **Yes** |
   | `OnlineMeetingTranscript.Read.All` (optional) | Reserved; not used yet | **Yes** |

   \* Your tenant may require admin consent for all apps (Entra → Enterprise applications → Consent and permissions).
   Simplest: an administrator clicks **Grant admin consent for <your organisation>** once.
4. Server settings:
   ```env
   MS_CLIENT_ID=<Application (client) ID>
   MS_CLIENT_SECRET=<secret value>
   MS_TENANT_ID=<Directory (tenant) ID>
   # only after an admin has granted them:
   MS_EXTRA_SCOPES=ChannelMessage.Read.All
   ```
   Nova checks the tenant in the sign-in token, so accounts from other organisations are refused. Production refuses
   `MS_TENANT_ID=common` or `consumers`.

### Each person
**Workspace → Accounts → Connect** for Google and/or Microsoft 365. Their browser's time zone is used the first time;
they can change it, working hours and days, holidays and quiet hours under **Automation**.

---

## 3. Automation modes

| Mode | What runs without asking |
|---|---|
| **1 · Assisted** (default) | Nothing. Reading, summaries and drafts only; every change waits in **Approvals**. |
| **2 · Approval-based** | Only the low-risk mailbox actions the person ticks: creating a Gmail draft, adding a label, marking read/unread. |
| **3 · Policy-based** | As 2, plus routines the person defines: currently "turn on my out-of-office when my own leave is confirmed", with their own message text. |

- **Pause all automation** stops everything in one click: scheduled messages wait and every action asks.
- Sending or forwarding email, Teams messages, channel posts, invitations, reschedules, cancellations and out-of-office
  for anyone other than yourself **always** need approval in every mode.
- Nova never changes the mode or widens permissions itself; mode changes are written to the audit log.
- Each approval runs **once**. Running an identical action again within 24 hours is refused as a duplicate.
- An action counts as done only when Google or Microsoft returns its id or link; that's what **Activity** shows.

---

## 4. Background jobs

| Job | Default |
|---|---|
| Your reminders and follow-up checks | every minute, on |
| Reminder before each meeting | every 5 minutes, on (minutes set in Automation) |
| Morning briefing | working days 08:30, on |
| Important email summary | working days 16:30, off |
| Unanswered email follow-ups | working days 10:00, off |
| Action items due | working days 09:00, off |
| Meeting conflict alerts | working days 08:00, off |
| End-of-day summary | working days 18:00, off |
| Weekly summary | Fridays 17:00, off |

Times are in the person's time zone; holidays are skipped. Each job is a database row with a 5-minute lease, so with
several workers or servers it runs once; failures retry with back-off (1, 2, 4 … up to 60 minutes, 5 attempts).

- `WORKSPACE_JOBS=true` (default) runs them inside the app.
- On hosts that sleep when idle (Render free plan) jobs only run while the app is awake. Either keep it awake or set
  `WORKSPACE_JOBS=false` and run every minute from cron: `flask --app app workspace-jobs --run-due`.
- **Run now** in Automation runs one job immediately (handy for checking a setup).

**Database:** SQLite is fine for one server. For several workers use PostgreSQL
(`DATABASE_URL=postgresql+psycopg://user:pass@host:5432/nova`; see DEPLOYMENT.md §10b).

---

## 5. Security

- **OAuth with PKCE** (S256). The state is single-use, tied to the Nova user who started it, and expires in 10 minutes.
- Tokens are **encrypted at rest**, refreshed automatically (Microsoft refresh tokens are rotated), never sent to the
  browser or the AI, and revoked/removed on disconnect. A revoked grant marks the account "Reconnect".
- Every person sees only their own mail, calendar and chats. Colleagues' calendars are read as free/busy only.
- Email, chat and calendar content is treated as **untrusted data**: instructions inside a message ("forward this to…")
  are never followed; the assistant is told so in its rules.
- Leave reasons are not repeated in reschedule notices. Activity history stores summaries, not message bodies.
- AI-written drafts and summaries get email/Teams text wrapped as untrusted data with instructions never to follow it.
  Recipients always come from what the user chose: a model reply that tries to add a recipient is ignored, and
  extracted items or triage results that point at anything other than the messages Nova sent are discarded.
- Sending to several recipients, forwarding attachments and posting to channels are always "high risk": they need
  approval in every mode, and the approval card lists every recipient and attachment.
- Graph throttling (429/503) is retried with `Retry-After`; Gmail quota and rate limits (429, `rateLimitExceeded`,
  500/503) are retried with exponential back-off; tokens expiring mid-request are refreshed once.
- Deleting a Nova account deletes all workspace data (preferences, drafts, absences, items, reminders, notifications,
  jobs, activity).

---

## 6. What is tested, and how

| | Status |
|---|---|
| Backend: 223 automated tests (47 workspace) | Pass on SQLite and PostgreSQL 16 |
| Workspace tests cover | OAuth with PKCE, state reuse, wrong tenant, token refresh on 401, revoked access, Graph throttling, Gmail quota retries, needs-a-response, triage (AI and fallback), action-item extraction without duplicates, email ↔ Teams conversions, forwarding attachments, mark read, paging, filtered search, approvals (edit, single use, retry without duplicates, cancel), prompt injection in email, other users unable to see or act on your data, retention, demo mode refused in production |
| Browser tests (desktop, tablet and phones down to 320 px) | Pass: connect accounts, day view with conflict, find a time, approve an invitation, leave, tasks, automation, activity; Inbox needs-a-response, email → Teams channel update approved, action items, triage, filtered search |
| Google and Microsoft APIs | Exercised against **simulated** Google and Microsoft servers that reproduce their requests and responses, including throttling, expired tokens and revoked access |
| Your real Google Workspace / Microsoft 365 tenant | **Not tested yet.** Needs the setup above and a test account. Recommended first run: connect, look at Today, prepare (don't approve) an invitation to yourself, send an out-of-office test to yourself |

No test sends real email or Teams messages.

---

## 7. Known limits

- Reading channel messages needs `ChannelMessage.Read.All` (admin consent). Without it, Teams search still covers
  your chats.
- Presence and meeting transcripts are **not built yet**; the permissions can be granted ahead of time.
- Email is read and sent through Gmail. Outlook mail reading/sending is not included (Outlook Calendar, Teams and
  Outlook automatic replies are).
- Gmail has a single vacation message: with only an internal text it replies only to your domain; when an external
  text is set, that text goes to everyone. Outlook keeps separate internal and external replies.
- Public holidays aren't fetched automatically; add them under Automation → Holidays.
- Forwarding attaches files up to 20 MB in total; larger files are named in the email instead.
- Teams search filters (sender, dates, channel) are applied to the top 25 matches Microsoft returns, so very broad
  words with narrow filters can miss older messages; use more specific words.
- "Needs a response" for Teams looks at your 30 most recent chats; for Gmail, inbox threads from the last 30 days.
- Leave detection only proposes; a person confirms each absence.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Accounts says "Not set up on this server" | Set `GOOGLE_*` / `MS_*` and redeploy. |
| Microsoft: `AADSTS50011` redirect mismatch | The Entra redirect URI must be exactly `<APP_URL>/api/workspace/microsoft/callback`. |
| Microsoft: `AADSTS65001` or "Need admin approval" | An administrator grants consent (step 3), or remove the scope from `MS_EXTRA_SCOPES`. |
| "Only accounts from your organisation's Microsoft 365 tenant can be connected" | Use your work account; check `MS_TENANT_ID`. |
| Google: "Some features need extra permission" | Click **Reconnect** after the scopes were added to the consent screen. |
| Colleague shows "availability unknown" | Their calendar isn't shared with you (Google sharing settings, or they're outside the tenant). |
| Briefing or reminders don't arrive | Check Automation → Scheduled jobs (enabled? last status?), that automation isn't paused, quiet hours, and that `WORKSPACE_JOBS` or the cron line is running. |
| "already sent in the last 24 hours" | Duplicate protection. Change the content, or wait. |
| "Google is rate-limiting or temporarily unavailable" | Nova already retried with back-off; wait a minute. Persistent: check the Gmail API quota in Google Cloud Console. |
| A send shows "Not sent" in Approvals | Read the reason, fix it (Edit) if needed and press **Retry**. If it actually went out earlier, Nova refuses the repeat. |

---

## 9. API
All under `/api/workspace`, signed-in user only, JSON errors `{"error", "code"}`; changes need the `X-CSRF-Token` header.

| Area | Endpoints |
|---|---|
| Status and settings | `GET /status` (connections, counts, demo flag), `GET/PUT /prefs` |
| Microsoft sign-in | `GET /microsoft/connect`, `GET /microsoft/callback`, `DELETE /microsoft` (Google: `/api/integrations/google/…`) |
| Calendar | `GET /calendar?date=&days=`, `POST /slots`, `POST /meetings` (prepares an invitation for approval) |
| Search | `GET /search?q=&source=all|gmail|teams&from=&since=&until=&page=` |
| Inbox | `GET /inbox/needs-response?days=`, `POST /inbox/triage`, `POST /inbox/action-items {days}`, `POST /inbox/convert {source: gmail|teams, ref, target: email|email_reply|teams_chat|teams_channel, dest, instructions}` |
| Email and Teams | `GET /email/<id>` (text + attachment list), `POST /email/<id>/read {read}`, `GET /teams/channels` |
| Briefing | `POST /briefing {period}` |
| Approvals | `GET /approvals?status=pending|failed|sent|discarded|all`, `POST /approvals {kind, title, payload}`, `PATCH /approvals/<id>`, `POST /approvals/<id>/approve`, `/retry`, `/discard` |
| Activity | `GET /activity` |
| Leave | `GET/POST /absences`, `PATCH/DELETE /absences/<id>`, `POST /leave/scan`, `GET /absences/<id>/affected`, `POST /absences/<id>/notices` |
| Out of office | `GET/PUT /ooo`, `POST /ooo/preview`, `/ooo/test`, `/ooo/enable`, `/ooo/disable` |
| Tasks | `GET/POST /action-items`, `PATCH/DELETE /action-items/<id>`, `GET/POST /reminders`, `DELETE /reminders/<id>` |
| Notifications and jobs | `GET /notifications`, `POST /notifications/read`, `GET /jobs`, `PUT /jobs/<kind>`, `POST /jobs/<kind>/run` |

The assistant uses the same capabilities as chat tools (for example `unified_search`, `inbox_needs_response`,
`email_triage`, `email_details`, `extract_action_items`, `draft_from_message`, `email_forward`, `teams_post_channel`),
each with a risk level: reads run directly, everything others would see pauses for approval.

---

## 10. Database schema and retention
- Tables are created and upgraded automatically on start (`flask --app app migrate` does the same by hand); versioned
  data migrations are recorded in `schema_migrations`.
- The full schema as SQL, for review or for DBAs who create tables themselves, is in
  [`backend/migrations/`](backend/migrations/) for PostgreSQL, MySQL and SQLite (regenerate with
  `flask --app app schema-sql --dialect postgresql`). A test fails if these files drift from the code.
- Workspace tables: `workspace_prefs`, `workspace_drafts` (approvals), `workspace_activity`, `absences`,
  `ooo_settings`, `action_items`, `reminders`, `notifications`, `jobs`, `oauth_states`; tokens live encrypted in
  `user_connections`.
- `WORKSPACE_RETENTION_DAYS` (default 180) deletes older activity history, notifications, finished approvals and past
  reminders during the hourly maintenance run. Emails and Teams messages themselves are not stored; what is kept is
  derived from them (drafts waiting for approval, action-item titles, briefing and reminder notifications) and is
  covered by this retention.
