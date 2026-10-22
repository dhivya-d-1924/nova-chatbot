# Security model

## Authentication and sessions
- Passwords are hashed with scrypt (per-password salt). Minimum 8 characters; common passwords and the account's own
  email are refused. Sign-in does the same work for unknown emails, so timing doesn't reveal which accounts exist.
- A successful sign-in creates a random 256-bit session token stored in an **HttpOnly, Secure (production),
  SameSite=Lax cookie scoped to `/api`**. Only its SHA-256 hash is stored in the `sessions` table, so a database leak
  doesn't expose usable tokens. JavaScript never sees the token; nothing auth-related is kept in `localStorage`.
- Sessions expire after `SESSION_DAYS` (absolute) or `SESSION_IDLE_HOURS` without use, and are revoked on sign-out,
  "sign out everywhere", password change/reset (other devices), MFA enrolment, account disable or demotion.
- **CSRF:** every state-changing request must carry `X-CSRF-Token`, a per-session random value returned by `/auth/me`
  and sign-in (synchronizer-token pattern, compared in constant time). Requests whose `Origin` isn't the app or an
  allowed origin are rejected too.
- **Login throttling:** per-IP limits on all auth endpoints, per-email limits on sign-in/reset/verification mail, and
  account lockout after `LOGIN_MAX_FAILURES` failures with progressively longer lockouts (up to 24 h). Admins can unlock.
- **Two-step verification (TOTP, RFC 6238)** with ±30 s tolerance, replay protection (a code can't be used twice) and
  eight single-use recovery codes (stored hashed). TOTP secrets are encrypted at rest (Fernet, key derived from
  `DATA_ENCRYPTION_KEY`).
- **Email verification** and **password reset** use signed, expiring, single-use links (reset links die once the
  password changes). Without SMTP, links are printed to the console in development only, never in production.
- **Admin bootstrap:** production never grants admin to the first registrant. Use `flask create-admin`, or
  `ADMIN_EMAILS` with mandatory email verification. The last active admin can't be removed or disabled.

## Authorization
- Role-based access: `user` and `admin`. Every admin route checks the role server-side; denied attempts are audited.
- Resource-level checks on every endpoint: conversations, messages, documents and their chunks, image attachments,
  memories, reports, feedback, approvals, sessions and exports are always filtered by the signed-in user's id **in the
  database query**, so other users' ids return 404 (no IDOR). Document ids sent with a question are re-checked for
  ownership before retrieval. Automated tests cover cross-user access for every resource type.

## AI agent safety
- Tools are filtered per user on every run (and again when an approval is resumed, so stored state can't widen
  permissions). The business-database tools are admin-only.
- Sensitive actions (email) **never run without an explicit Approve click** by the owner. Approvals are single-use
  (atomically claimed), bound to the user and expire after `APPROVAL_TTL_MINUTES`. Specialist sub-agents never get
  sensitive tools.
- Prompt-injection containment: web pages, documents, database rows and tool output are fenced as `<untrusted>` data
  and the model is told not to follow instructions inside them. More importantly, permissions live in code, not in the
  prompt, and the approval step shows the exact recipient/subject/body; injected text can't send anything by itself.
- Bounded execution: `AGENT_MAX_STEPS` model rounds, `TOOL_TIMEOUT_SECONDS` per tool, `AGENT_TIMEOUT_SECONDS` per run,
  real cancellation when the user presses Stop or disconnects.
- Cost controls: per-user daily token limits, a global monthly budget, per-user concurrency limits and rate limits.

## Web and network
- **SSRF:** the page reader accepts only http(s), resolves the host and rejects private, loopback, link-local and
  metadata addresses, then **re-checks the address the socket actually connected to** (defeats DNS rebinding) and
  every redirect hop. Environment proxies are ignored unless `FETCH_PROXY` is set. `ALLOWED_DOMAINS` can restrict
  browsing further.
- **Read-only SQL tool:** single `SELECT`/`WITH` statement, keyword deny-list, table allow-list, 50-row cap and a
  read-only connection. For real data, connect with a database user that only has `SELECT` on the exposed tables.
- **XSS:** React escapes content; Markdown is rendered without raw HTML, links are limited to http(s)/mailto with
  `rel="noopener noreferrer nofollow"`, and images from model output are never loaded. A strict Content-Security-Policy
  (`script-src 'self'`, no inline scripts, `frame-ancestors 'none'`) backs this up.
- **Headers:** CSP, HSTS (when HTTPS), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `Cache-Control: no-store` on API responses.
- **CORS:** explicit allow-list with credentials; `*` is refused at startup.
- **Injection:** all database access uses SQLAlchemy Core with bound parameters; `LIKE` searches escape wildcards.

## Uploads
- Request-size cap (`MAX_UPLOAD_MB`), one file per request, per-user document and unsent-image limits, upload rate
  limits.
- File type is decided from the **content** (magic bytes), not the name; executables and scripts are refused.
  DOCX archives are checked for zip bombs; PDF page counts are capped; image decompression bombs are refused.
- Original files are not stored and never executed or served back; only extracted text (and sniffed PNG/JPEG/WEBP/GIF
  chat images, served with `nosniff`) is kept.

## Privacy and data handling
- Users can export all their data and delete their account (all conversations, documents, images, memories and
  reports are removed; logs keep only anonymised counts).
- Memory is opt-in and the model is told never to save sensitive data; admins can't read memories.
- Audit and tool logs never store passwords, tokens, email bodies or message contents.
- Retention: logs are deleted after `LOG_RETENTION_DAYS`; optional `CHAT_RETENTION_DAYS` for conversations.
- Errors returned to clients never include stack traces or internal details.

## Workspace agent (Gmail, Microsoft Teams, calendars)
Security review of the workspace integrations; details in [WORKSPACE.md](WORKSPACE.md#5-security).

| Risk | Control |
|---|---|
| Stolen or replayed sign-in | OAuth 2.0 authorization code with PKCE (S256); state is random, stored hashed, single-use, bound to the Nova user, 10-minute expiry. Microsoft tenant checked in the id_token; Google domain via `GOOGLE_ALLOWED_DOMAIN`. Nova never asks for or stores passwords. |
| Token theft | Access and refresh tokens encrypted at rest (Fernet), never returned to the browser, the AI or logs; refreshed server-side; revoked on disconnect; a revoked grant deletes the stored tokens. |
| Over-broad access | Delegated permissions only, as the signed-in user; least-privilege scopes listed in WORKSPACE.md; admin-consent scopes are opt-in (`MS_EXTRA_SCOPES`). Drive is read-only; Gmail uses `gmail.modify` (no permanent delete). |
| Cross-user leakage | Every query is filtered by the session's user id; tokens are per user; approvals, items and drafts of another user return 404. Covered by tests. |
| Prompt injection in mail, chats, attachments | Content is passed to the model as untrusted data with explicit rules; the agent cannot send anything without the user's approval in chat or the Approvals center; recipients come only from the user; model output is validated (ids, categories, recipients) before use. |
| Unwanted or duplicate sends | Anything others see is high risk and always needs approval (preview shows recipients, text, attachments, destination); approvals are single-use; identical sends within 24 h are refused, also on Retry; only provider-confirmed results are reported as sent. |
| Abuse and quotas | Per-user rate limits on search, AI features and approvals; provider throttling handled with back-off. |
| Data retention | Workspace history, notifications and finished drafts expire after `WORKSPACE_RETENTION_DAYS`; account deletion removes all workspace data. |
| Demo mode in production | `WORKSPACE_DEMO` is refused when `APP_ENV=production`. |

Residual risks: a person can still approve a bad draft (the preview is the safeguard); provider-side sharing settings
decide what free/busy and search can see; AI summaries can be wrong, so facts and suggestions are labelled separately.

## Reporting
Please report vulnerabilities privately to the maintainers rather than in a public issue.
