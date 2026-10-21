# Connecting Nova to your internal apps and Google Workspace

There are two ways Nova can work with your other systems:

| | Internal apps (any REST API) | Google Workspace |
|---|---|---|
| Who sets it up | An administrator, in **Admin → Apps** | The administrator adds a Google OAuth client once; then **each person** clicks *Connect Google* in **Account → Apps** |
| Whose data | The app's data, via a service key | Each person's own Calendar, Drive and Gmail |
| What Nova can do | Exactly the actions you define (look up an employee, check stock, raise a ticket…) | Read calendar, Drive and Gmail; create calendar events |
| Changes | Every POST/PUT/PATCH/DELETE action asks the user to **approve** first | Creating an event asks the user to **approve** first |

People just ask in chat ("What's my leave balance?", "Raise a ticket: my laptop won't charge", "What's on my
calendar tomorrow?"). Nova picks the right action, and you see each step in the reply. Agent tools must be switched on
in the chat (the default).

---

## 1. Internal apps (any REST API)

### Add an app
In **Admin → Apps → Add an app**, fill in:

| Field | Example | Notes |
|---|---|---|
| Name | HR system | Shown to people |
| Short name | `hr` | Lowercase; tool names start with it (`hr__get_employee`). Can't be changed later. |
| What it's for | Employees, leave balances and HR tickets | The assistant reads this to decide when to use the app |
| Base URL | `https://hr.internal.example.com/api` | Every action's path is added to this |
| Authentication | API key in a header → `X-API-Key` | Also: bearer token, basic (`user:password`) or none |
| Token / API key | `••••` | Stored encrypted; never shown again, never sent to the AI or the browser |
| Who can use it | Everyone / Administrators only | |
| Send who is asking | on | Adds `X-Nova-User-Email` and `X-Nova-User-Id` so your app can apply each person's own permissions |

### Add actions
Each action is one endpoint the assistant may call:

| Action | Method | Path | Parameters |
|---|---|---|---|
| `get_employee`, "Look up an employee" | GET | `/employees/{employee_id}` | `employee_id` (path) |
| `leave_balance`, "Check leave balance" | GET | `/leave/balance` | `email` (query) |
| `create_ticket`, "Raise an HR ticket" | POST | `/tickets` | `title` (body, required), `priority` (body, integer) |

- Write a clear **"When to use it"** sentence; that's what the assistant reads.
- Parameter types (`string`, `number`, `integer`, `boolean`) are checked before the request is sent.
- Values for `{placeholders}` in the path are URL-encoded, so a value can't change the path.
- Use **Test** to run an action with sample values. It shows the HTTP status and the response.
- **Turn off** hides an action from the assistant immediately, without deleting it.

### Safety built in
- Only administrators can add or change apps, actions and keys, and every change is recorded in the **audit log**.
- POST/PUT/PATCH/DELETE actions **always** need the user's approval, which works once and expires. GET actions can
  optionally require it too.
- Requests can only go to the app's own base URL. Redirects are not followed. Responses over 200 KB are cut, and the
  assistant treats the response as data, not instructions.
- Private (intranet) addresses are allowed, because that's where internal apps live. Cloud metadata and
  link-local addresses (`169.254.x.x` etc.) are always blocked. To pin the hosts admins may use, set
  `INTEGRATION_ALLOWED_HOSTS=hr.internal.example.com,crm.internal.example.com`.
- Every call is in **Admin → Usage → tool success** and the tool log, with who made it and the arguments.
- **Per-person permissions:** the key you enter is a service key, so your app should check `X-Nova-User-Email` when
  some data must only be shown to its owner. Only Nova holds the key, so the header can be trusted.

### Reaching apps on your private network
Nova must be able to reach the app's address. On Render or another cloud host that usually means the app has a
public HTTPS address (protected by its key), or you run Nova inside your network (Docker on an internal server,
see DEPLOYMENT.md), or you connect the two with a VPN/tunnel.

---

## 2. Google Workspace

### Administrator: create the OAuth client (once)
1. Go to **console.cloud.google.com** and create a project (or pick one).
2. Under **APIs & Services → Library**, enable **Google Calendar API**, **Google Drive API** and **Gmail API**.
3. Under **APIs & Services → OAuth consent screen**, choose User type **Internal**, so only your Workspace domain
   can use it and Google's app review isn't needed. Add the scopes:
   - `.../auth/calendar.events`
   - `.../auth/drive.readonly`
   - `.../auth/gmail.readonly`
   - `openid`
   - `email`
4. Under **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**
   - Authorized redirect URI: `https://<your-nova-address>/api/integrations/google/callback`
     (for example `https://nova-chatbot-ye2s.onrender.com/api/integrations/google/callback`)
5. Set these on the server and redeploy:
   ```env
   GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-...
   GOOGLE_ALLOWED_DOMAIN=yourcompany.com
   ```
   `APP_URL` must be the same address you used in the redirect URI.

### Each person
**Account → Apps → Connect Google**, then sign in with their work account and allow access. To stop, click
**Disconnect**, which also revokes Nova's access at Google.

Then ask, for example:
- "What meetings do I have tomorrow?"
- "Find the travel policy in my Drive and summarise it"
- "Any emails from finance this week?"
- "Book 30 minutes with priya@yourcompany.com on Friday at 3pm". Nova asks you to approve before it creates the event.

### Safety built in
- Each person connects **their own** account; Nova never sees anyone else's Google data.
- Tokens are encrypted at rest, refreshed automatically, and revoked on disconnect. If someone removes Nova's access
  in their Google account, Nova notices and asks them to reconnect.
- The sign-in link is signed, expires after 10 minutes, and only works for the Nova user who started it.
- `GOOGLE_ALLOWED_DOMAIN` refuses personal Gmail and other companies' accounts.
- Drive and Gmail access is **read-only**. The only write action, creating a calendar event, always needs approval.

---

## Settings reference

| Variable | Default | Purpose |
|---|---|---|
| `INTEGRATION_TIMEOUT_SECONDS` | 15 | How long to wait for an internal app |
| `INTEGRATION_ALLOWED_HOSTS` | (any) | Comma-separated hosts admins may use as base URLs |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | – | Turns on Google Workspace |
| `GOOGLE_ALLOWED_DOMAIN` | (any) | Only this Workspace domain can connect (recommended) |

## API (summary)
- Admin: `GET/POST /api/admin/integrations`, `PATCH/DELETE /api/admin/integrations/<id>`,
  `POST /api/admin/integrations/<id>/actions`, `PATCH/DELETE /api/admin/integrations/<id>/actions/<action>`,
  `POST /api/admin/integrations/<id>/actions/<action>/test`
- Everyone: `GET /api/integrations`, `GET /api/integrations/google/connect`, `GET /api/integrations/google/callback`,
  `DELETE /api/integrations/google`
