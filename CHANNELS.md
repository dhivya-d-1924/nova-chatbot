# Nova in WhatsApp, Microsoft Teams and email

Staff can chat with Nova from WhatsApp, a 1:1 chat in Microsoft Teams, or by email. They get the same assistant as on
the web: memory, agent tools, and their own documents. Each channel switches on when its settings are filled in. A
person can only use a channel after linking it to their own Nova account.

| | WhatsApp | Microsoft Teams | Email |
|---|---|---|---|
| Provider | Meta WhatsApp Cloud API | Azure Bot (Bot Framework) | Any IMAP mailbox + your SMTP settings |
| How it reaches Nova | Webhook `POST /api/channels/whatsapp/webhook` | Webhook `POST /api/channels/teams/messages` | Nova checks the mailbox every 60 s |
| Proof the message is genuine | HMAC signature with your app secret | Microsoft-signed token (JWT), checked against your app ID and tenant | DMARC/DKIM result recorded by your mail server |
| Actions that need approval (e.g. sending email) | Reply **YES** / **NO** | Reply **YES** / **NO** | Not available by email |
| Conversation | One rolling chat (`new` starts another) | One rolling chat (`new` starts another) | One chat per email thread |

You need a public **HTTPS** address for the webhooks, such as your Render URL (`https://nova-chatbot-ye2s.onrender.com`)
or your own domain. Email replies need working SMTP. Render's free plan blocks outgoing email, so use a paid instance
for the email channel.

---

## How staff use it

1. In Nova, open **Account → Apps** and click **Link WhatsApp**, **Link Microsoft Teams** or **Link Email**.
2. Nova shows a one-time message such as `link 482913`, valid for 10 minutes. Send it from the app you are linking.
   On phones, the **Open WhatsApp** button opens the chat with the message already typed.
3. Nova replies "✅ Linked". After that, just ask questions.

Commands you can send in any channel:

| Message | What it does |
|---|---|
| `docs <question>` | Answers from your uploaded documents, with sources |
| `new` | Starts a fresh chat (WhatsApp/Teams). By email, a new email starts a new chat and a reply continues the thread. |
| `YES` / `NO` | Approves or cancels an action Nova asked about (WhatsApp/Teams) |
| `unlink` | Disconnects this app from your account (you can also do this in Account → Apps) |
| `help` | Shows the commands |

Chats from WhatsApp, Teams and email also appear in the web app's sidebar ("WhatsApp chat", "Teams chat", "Email: …").

---

## 1. WhatsApp (Meta WhatsApp Cloud API)

1. Go to **developers.facebook.com → My Apps → Create app**, choose the WhatsApp / Business use case, and add the
   **WhatsApp** product. Meta creates a **test phone number** for you.
2. Under **WhatsApp → API Setup**, copy:
   - the **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID`
   - the **access token** → `WHATSAPP_ACCESS_TOKEN`. The token shown there expires after about 24 hours. For a token
     that doesn't expire, go to **Meta Business Settings → Users → System users**, add a system user, assign it the
     app with the `whatsapp_business_messaging` and `whatsapp_business_management` permissions, and generate a token.
   - While testing, add your own phone under **To** as an allowed recipient.
3. Under **App settings → Basic**, copy the **App secret** → `WHATSAPP_APP_SECRET`.
4. Choose a random verify token (16+ characters), for example the output of
   `python -c "import secrets;print(secrets.token_urlsafe(24))"` → `WHATSAPP_VERIFY_TOKEN`.
5. Set the variables on your server (Render: **Environment**), plus `WHATSAPP_DISPLAY_NUMBER` (the number staff
   should message, e.g. `+1 555 010 0000`). Then deploy.
6. In Meta, open **WhatsApp → Configuration → Webhook → Edit**:
   - Callback URL: `https://<your-domain>/api/channels/whatsapp/webhook`
   - Verify token: the same value as `WHATSAPP_VERIFY_TOKEN`
   - Click **Verify and save**, then **Subscribe** to the **messages** field.
7. Test it: message the WhatsApp number from your phone. Nova should reply asking you to link your account.

To use your company's own number instead of the test number, add and verify it in WhatsApp Manager. Meta asks for
business verification for full production use. Nova only replies to messages users send it, inside WhatsApp's
24-hour service window, so message templates are not needed.

## 2. Microsoft Teams (Azure Bot)

You need an Azure subscription and a Microsoft 365 admin who can approve a Teams app.

1. In **portal.azure.com**, choose **Create a resource → Azure Bot**:
   - Pricing tier: **Free (F0)** is enough for Teams.
   - Type of App: **Single Tenant**. Creation type: **Create new Microsoft App ID**.
2. Open the new bot resource:
   - **Configuration → Messaging endpoint**: `https://<your-domain>/api/channels/teams/messages`, then **Apply**.
   - Copy the **Microsoft App ID** → `TEAMS_APP_ID` and the **App Tenant ID** → `TEAMS_TENANT_ID`.
   - Click **Manage Password** (next to the app ID), then **Certificates & secrets → New client secret**. Copy the
     secret's **Value** → `TEAMS_APP_PASSWORD`. It's shown only once.
3. In the bot resource, go to **Channels → Microsoft Teams**, accept the terms and click **Apply**.
4. Set the three variables on your server and deploy.
5. Create the Teams app. In **dev.teams.microsoft.com → Apps → New app**, fill in the basic info. Under
   **App features → Bot**, choose **Enter a bot ID**, paste the App ID, select the **Personal** scope and save.
   Then go to **Publish → Publish to your org**, and a Teams admin approves it in the **Teams admin center**. For a
   quick test, you can instead download the app package and use **Upload a custom app** in Teams, if your
   organisation allows it.
6. In Teams, open the app and send **hi**. Nova replies with linking instructions.

Nova answers only in **1:1 chats**. Messages in team channels or group chats get a short "please message me directly"
reply, so private answers never appear where others can read them. Messages from other organisations (tenants) are
rejected.

## 3. Email

Use a **dedicated mailbox**, for example `nova@yourcompany.com`, and not a person's inbox. Nova marks messages as read
and replies to them.

| Setting | Gmail / Google Workspace |
|---|---|
| `EMAIL_CHANNEL_IMAP_HOST` | `imap.gmail.com` (port 993) |
| `EMAIL_CHANNEL_USER` / `EMAIL_CHANNEL_PASSWORD` | the mailbox address and an **App Password** (needs 2-Step Verification on that account) |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` | `smtp.gmail.com`, port 587, **the same mailbox** so replies come from the address staff wrote to |
| `EMAIL_CHANNEL_AUTHSERV_ID` | `mx.google.com` |

For other providers, find `EMAIL_CHANNEL_AUTHSERV_ID` by opening any received email's raw source ("Show original").
It's the first word of the top-most `Authentication-Results:` header, for example `mx.zohomail.com`. Microsoft 365
writes no server name there, so use `first`.

**Microsoft 365 / Exchange Online:** Microsoft has turned off password sign-in for IMAP, and this connector signs in
with a password (OAuth isn't supported yet). Use a Google Workspace, Zoho or other IMAP mailbox with app passwords
for the email channel, or ask for an OAuth version.

How it stays safe:
- The From address of an email is easy to fake, so Nova only answers mail that **your** mail server marked as passing
  DMARC, or DKIM for the sender's domain. Fake mail is ignored and never answered.
- Unknown senders get no reply. Bounces, out-of-office replies and mailing lists are ignored. Nova's replies carry
  `Auto-Submitted: auto-replied`, so two auto-responders can't loop.
- Actions that need approval, such as Nova sending an email for you, aren't offered by email. Email can be forwarded,
  so a YES there doesn't prove it's you. Use the web app, WhatsApp or Teams for those.

Polling: every web worker can poll, but a lease in the database lets only one of them read the mailbox at a time.
To poll from cron instead, set `EMAIL_CHANNEL_POLLER=false` and run
`flask --app app channels --poll-email` every minute.

---

## Checking the setup

- `flask --app app channels` (on Render: **Shell**) lists each channel as enabled or disabled, with its webhook
  address. In production, Nova refuses to start if a channel is only half configured.
- **Admin → Channels** shows the same information, plus how many people have linked each app.
- Linking, unlinking and approvals are recorded in **Admin → Audit log**. AI usage from channels counts towards each
  user's daily limit and the monthly budget, like web chats.

## Security summary

- **Identity:** only accounts linked with a one-time code shown to the signed-in user can chat. Codes are stored
  hashed, work once, expire after 10 minutes, and allow 5 attempts per sender every 15 minutes.
- **Provider checks:** WhatsApp HMAC signature (constant-time comparison); Teams JWT signature, issuer, audience,
  expiry, serviceUrl and tenant; email DMARC/DKIM from your own mail server.
- **Data isolation:** each person only reaches their own chats, documents and memory. One phone, Teams account or
  email address can be linked to only one Nova user.
- **Disabled users** are refused on every channel. **Deleting an account** removes its links.
- **Retries:** providers resend webhooks; repeated message ids are handled once.
