# Nova – AI Agent Chatbot

A secure, production-oriented AI assistant: React + Vite + Tailwind frontend, Flask backend, streaming replies, document
Q&A with citations, agent tools with human approval, memory, voice, image understanding and an admin dashboard.
Works with any OpenAI-compatible provider (OpenAI, Gemini, OpenRouter, Groq, Together, Ollama, LM Studio…).

- **Upgrading from the previous version?** Read [UPGRADE NOTES](#upgrading-from-the-previous-version) below.
- **Deploying?** See [DEPLOYMENT.md](DEPLOYMENT.md). **Security model:** [SECURITY.md](SECURITY.md).
- **What changed and why:** [AUDIT.md](AUDIT.md).
- **WhatsApp, Microsoft Teams and email:** [CHANNELS.md](CHANNELS.md).
- **Internal apps (any REST API) and Google Workspace:** [INTEGRATIONS.md](INTEGRATIONS.md).

## Requirements
Node 18+ (20 recommended) and Python 3.10+. Optional: Redis (shared rate limits with several workers), MySQL,
Tesseract (OCR for scanned PDFs and images).

## Local development

**1. Backend**
```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # add -r requirements-dev.txt for tests and lint
python setup_env.py                # writes backend/.env and tests your AI key (or: cp .env.example .env and edit)
python app.py                      # http://localhost:5000
```
Database tables are created and upgraded automatically on start (`flask --app app migrate` does it explicitly).
In development the **first account you register becomes the administrator**.

**2. Frontend** (second terminal)
```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173
```
Vite proxies `/api` to Flask, so cookies, CORS and streaming work without extra setup.

**Testing on phones and tablets:** run `npm run dev -- --host` and open the printed network address (for example
`http://192.168.1.20:5173`) on the device. Add that address to `ALLOWED_ORIGINS` in `backend/.env`. Voice input
needs HTTPS (or localhost) on most mobile browsers.

## Tests
```bash
cd backend && pytest -q                    # 176 API, security, agent, RAG, study, channel, integration, migration and ops tests
cd frontend && npm test                    # unit/component tests (Vitest + Testing Library)
cd frontend && E2E_PYTHON=../backend/.venv/bin/python npm run test:e2e   # Playwright end-to-end
```
On Windows (PowerShell): `$env:E2E_PYTHON="..\backend\.venv\Scripts\python.exe"; npm run test:e2e`.
End-to-end tests start the real backend with a fake OpenAI-compatible provider (`backend/tests/fake_llm.py`) on a fresh
database, so they need no API key. Projects cover desktop Chrome, a 320 px phone, Android, iPhone (portrait and
landscape) and iPad viewports; set `E2E_ALL_BROWSERS=1` (after `npx playwright install`) to add Firefox, WebKit/Safari
and Edge. Lint: `ruff check .` (backend) and `npm run lint` (frontend). CI runs all of these (`.github/workflows/ci.yml`).

## Features

**Chat** – streaming replies, stop, retry, regenerate, edit an earlier message (later replies are replaced), copy,
👍/👎 feedback, timestamps, Markdown with code blocks and tables (raw HTML is never rendered), conversation search,
rename, pin, archive, export (Markdown/JSON) and delete. Interrupted replies are kept and clearly marked. Long
conversations are summarised automatically so older context isn't silently lost.

**Accounts & security** – HttpOnly session cookies (no tokens in browser storage) with server-side revocation, CSRF
protection, optional two-step verification (authenticator app + recovery codes), email verification, password reset,
login lockout, session list ("sign out other devices"), data export and account deletion. Settings (theme, voice,
assistant mode) are stored on the server and follow you to every device.

**Document Q&A (RAG)** – PDF, DOCX, TXT, MD, CSV, and scanned PDFs/images when OCR is installed. Answers cite the
file and page/section; click a citation to read the passage. Keyword (BM25) search always works locally; set
`EMBEDDING_MODEL` to add semantic search (hybrid ranking). Duplicate uploads are detected and re-uploads of the same
file name are versioned. Documents are private to their owner at upload, retrieval and answer time.

**Agent tools** – web search, page reading, calculator, date/time, reports, human handoff, email (always asks for your
approval, single-use, expires), read-only business database (admins). Specialist agents (research, coding, data) and
automatic delegation. Bounded by step, tool-time and total-time limits; tool output is treated as untrusted data.

**Internal apps and Google Workspace** – administrators connect any internal REST API (HR, CRM, tickets…) in
Admin → Apps and choose the actions the assistant may use; reads run directly, changes always need the user's approval,
keys are encrypted and never reach the AI. Each person can connect their own Google account (Calendar, Drive, Gmail;
creating events needs approval). Setup: [INTEGRATIONS.md](INTEGRATIONS.md).

**Workspace agent (Gmail, Teams, calendars, leave)** – connect Google Workspace and/or Microsoft 365 and open
**Workspace**: an **Inbox** of everything waiting for your response across Gmail and Teams, AI-drafted replies and
email ↔ Teams conversions (drafts only), action items found in both with source links, email triage, one search
across Gmail and Teams with filters, forwarding with attachments; one day view across Google and Outlook calendars
with conflicts, a briefing (facts kept apart from suggestions), free-time search across colleagues' calendars, meeting invitations, leave and absence tracking (scans
Gmail and Teams, finds affected meetings, drafts reschedule notices), out-of-office for Gmail and Outlook, action items,
reminders and follow-ups, notifications (app, email, WhatsApp, Teams; quiet hours) and scheduled jobs (morning
briefing, digests, end-of-day and weekly summaries). Three automation modes, **Assisted** by default: nothing anyone
else can see is sent without your approval, every action is logged with the provider's confirmation, and one click
pauses everything; failed sends can be retried without ever sending twice. Try it without accounts with
`WORKSPACE_DEMO=true` (development only). Setup: [WORKSPACE.md](WORKSPACE.md).

**Study mode** – create a subject, add the syllabus and study material, and get a day-by-day study plan (5 days by
default, up to 14) with topics, goals and tasks you can tick off. Generate study material for any topic or plan day
(notes, key terms, revision list, practice questions with answers, all cited). The subject tutor chat answers **only
from that subject's material**: off-topic or uncovered questions are not sent to the AI and go into the **open doubts
index**, where you can resolve them with a note.

**WhatsApp, Teams and email** – staff link their own accounts (Account → Apps) and chat with the same assistant
from WhatsApp (Meta Cloud API), a 1:1 Teams chat (Azure Bot) or a dedicated mailbox. Provider signatures are verified,
approvals work with YES/NO, and `docs <question>` searches your documents. Setup: [CHANNELS.md](CHANNELS.md).

**Images, voice, memory** – attach/paste/drop images; dictate and listen in 12 languages (browser speech APIs); opt-in
memory you can view and delete.

**Admin dashboard** – overview, users (search, filters, pagination, roles, disable, unlock, sign out), support
requests, AI usage (tokens, estimated cost, latency p50/p95, failures, per model/user, tool success rates), audit log,
errors, and quality evaluation.

## Configuration
All settings live in `backend/.env`; [`backend/.env.example`](backend/.env.example) documents every variable with safe
placeholders. The most important:

| Variable | Purpose |
|---|---|
| `APP_ENV` | `development` (default), `testing` or `production`. Production refuses unsafe settings. |
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` | AI provider. The key stays on the server. |
| `SECRET_KEY` | Required in production (signs reset/verification links). |
| `DATABASE_URL` | `sqlite:///chat.db` (default), `postgresql+psycopg://user:pass@host:5432/db` or `mysql+pymysql://user:pass@host:3306/db`. |
| `APP_URL`, `ALLOWED_ORIGINS` | Public frontend address and the origins allowed to call the API. |
| `REDIS_URL` | Shared rate limits/quotas across workers (recommended in production). |
| `SMTP_*`, `REQUIRE_EMAIL_VERIFICATION` | Email for verification and reset links. |
| `DAILY_TOKEN_LIMIT_PER_USER`, `MONTHLY_BUDGET_USD`, `LLM_PRICE_*` | Cost controls and cost estimates. |
| `EMBEDDING_MODEL`, `OCR_ENABLED` | Semantic search; OCR for scans. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `INTEGRATION_ALLOWED_HOSTS` | Google Workspace and internal app limits (see INTEGRATIONS.md). |
| `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_TENANT_ID`, `MS_EXTRA_SCOPES`, `WORKSPACE_JOBS`, `WORKSPACE_RETENTION_DAYS`, `WORKSPACE_DEMO` | Microsoft 365, background jobs, retention and demo mode for the workspace agent (see WORKSPACE.md). |
| `WHATSAPP_*`, `TEAMS_*`, `EMAIL_CHANNEL_*` | WhatsApp, Microsoft Teams and email channels (see CHANNELS.md). |

## API (summary)
All endpoints are under `/api`, return JSON errors as `{"error": "...", "code": "..."}`, and (except sign-in, health and
public config) need the session cookie; state-changing requests also need the `X-CSRF-Token` header returned by
`/auth/me` and sign-in.

| Area | Endpoints |
|---|---|
| Health | `GET /health`, `GET /health/live`, `GET /health/ready` |
| Auth | `POST /auth/register`, `/auth/login`, `/auth/mfa`, `/auth/logout`, `/auth/logout-all`, `/auth/forgot`, `/auth/reset`, `/auth/verify`, `/auth/resend-verification`; `GET /auth/me`, `/auth/config` |
| Account | `PATCH /account/profile`, `POST /account/password`, `GET/DELETE /account/sessions[/<id>]`, `POST /account/mfa/{setup,enable,disable}`, `POST /account/verify-email`, `GET /account/export`, `DELETE /account`, `GET/PUT /settings`, `GET /usage` |
| Conversations | `GET/POST /conversations` (`?q=`, `?archived=1`), `GET/PATCH/DELETE /conversations/<id>`, `DELETE /conversations/<id>/messages`, `GET /conversations/<id>/export?format=md|json`, `GET /search?q=` |
| Chat | `POST /chat/stream` (SSE: `user`, `delta`, `step`, `done`, `error`; `: ping` heartbeats), `POST /chat`, `POST /chat/resume`, `GET /tools`, `POST /messages/<id>/feedback` |
| Documents | `GET/POST /documents`, `GET /documents/config`, `DELETE /documents/<id>`, `POST /documents/<id>/reindex` |
| Integrations | `GET /integrations`, `GET /integrations/google/connect`, `GET /integrations/google/callback`, `DELETE /integrations/google`; admin `GET/POST /admin/integrations`, `PATCH/DELETE /admin/integrations/<id>`, `POST/PATCH/DELETE /admin/integrations/<id>/actions[/<action>]`, `POST …/actions/<action>/test` |
| Workspace | `GET /workspace/status`, `GET/PUT /workspace/prefs`, `GET /workspace/microsoft/connect`, `GET /workspace/microsoft/callback`, `DELETE /workspace/microsoft`, `GET /workspace/calendar`, `POST /workspace/slots`, `POST /workspace/meetings`, `GET /workspace/search` (`source`, `from`, `since`, `until`, `page`), `GET /workspace/inbox/needs-response`, `POST /workspace/inbox/{triage,action-items,convert}`, `GET /workspace/email/<id>`, `POST /workspace/email/<id>/read`, `GET /workspace/teams/channels`, `POST /workspace/briefing`, `GET/POST /workspace/approvals`, `PATCH /workspace/approvals/<id>`, `POST /workspace/approvals/<id>/{approve,retry,discard}`, `GET /workspace/activity`, `GET/POST /workspace/absences`, `PATCH/DELETE /workspace/absences/<id>`, `POST /workspace/leave/scan`, `GET /workspace/absences/<id>/affected`, `POST /workspace/absences/<id>/notices`, `GET/PUT /workspace/ooo`, `POST /workspace/ooo/{preview,test,enable,disable}`, `GET/POST /workspace/action-items`, `PATCH/DELETE /workspace/action-items/<id>`, `GET/POST /workspace/reminders`, `DELETE /workspace/reminders/<id>`, `GET /workspace/notifications`, `POST /workspace/notifications/read`, `GET /workspace/jobs`, `PUT /workspace/jobs/<kind>`, `POST /workspace/jobs/<kind>/run` |
| Study | `GET/POST /subjects`, `GET/PATCH/DELETE /subjects/<id>`, `POST/DELETE /subjects/<id>/documents[/<doc>]`, `POST /subjects/<id>/plan`, `PATCH /subjects/<id>/plan/days/<n>`, `GET/POST /subjects/<id>/notes`, `GET/DELETE /subjects/<id>/notes/<note>`, `POST /subjects/<id>/chat`, `GET /doubts`, `POST /subjects/<id>/doubts`, `PATCH/DELETE /doubts/<id>` |
| Channels | `GET /channels`, `POST /channels/<whatsapp|teams|email>/link-code`, `DELETE /channels/links/<id>`; webhooks `GET/POST /channels/whatsapp/webhook`, `POST /channels/teams/messages`; `GET /admin/channels` |
| Other | `POST/GET/DELETE /attachments[/<id>]`, `GET/PUT/POST/DELETE /memory…`, `GET /reports[/<id>]`, `GET/POST /handoffs` |
| Admin | `GET /admin/stats`, `/admin/usage`, `/admin/users`, `/admin/handoffs`, `/admin/audit`, `/admin/errors`, `/admin/evals…`; `PATCH /admin/users/<id>`, `POST /admin/users/<id>/revoke-sessions`, `POST /admin/handoffs/<id>/resolve` |

## Upgrading from the previous version
1. Back up `backend/chat.db` (or your MySQL database).
2. `pip install -r backend/requirements.txt` (adds `cryptography` and `Pillow`) and `npm install` in `frontend`.
3. Start the backend: missing columns/tables are added automatically and existing users are marked verified. Existing
   chats, documents, memories and reports are kept.
4. Everyone signs in again once (sessions moved from browser storage to secure cookies).
5. In production, set `APP_ENV=production`, `SECRET_KEY`, `APP_URL` and `ALLOWED_ORIGINS`. The "first account
   becomes admin" rule is off in production: existing admins stay admins; create new ones with
   `flask --app app create-admin --email you@example.com`.
6. The backend code moved into the `backend/nova/` package; `python app.py` and `gunicorn app:app` work as before.

## Keyboard & accessibility
Enter sends, Shift+Enter adds a new line. Every control is keyboard reachable with visible focus; dialogs trap focus,
close with Esc and return focus; icon buttons have labels; form errors are announced; inputs use 16 px text on phones
(no iOS zoom); touch targets are at least 44 px; animations respect reduced-motion settings; layouts work from 320 px
wide in portrait and landscape, with safe-area spacing for notched phones.
