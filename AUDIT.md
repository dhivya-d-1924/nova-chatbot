# Security & functionality audit, and what was changed

Scope: the complete uploaded project (Flask backend `app.py` + `tools.py`, React/Vite frontend, configuration).
The original was a solid base: per-user query scoping, SSRF checks, image sniffing, an approval gate for email and safe
Markdown were already in place. Every existing feature was kept (streaming, document Q&A with citations, agent tools,
approvals, specialist agents, memory, voice, images, evaluation, admin dashboard, handoffs).

## 1. Findings in the original code (prioritised)

### Critical / high
| # | Finding | Risk | Fix |
|---|---|---|---|
| 1 | 30-day bearer token stored in `localStorage`; logout only deleted it in the browser | Any XSS steals sessions; stolen tokens can't be revoked | Random session token in an **HttpOnly/Secure/SameSite cookie**, stored hashed server-side, revoked on logout, password change/reset, disable, demotion; idle + absolute expiry; device list |
| 2 | First registered account became admin, in every environment | Whoever registers first on a public deployment owns it | Off in production; `flask create-admin` CLI; `ADMIN_EMAILS` only after email verification; last admin can't be removed |
| 3 | Rate limiter keyed on client-supplied `X-Forwarded-For` | Trivial bypass of all rate limits | `ProxyFix` with `TRUSTED_PROXY_COUNT`; header ignored otherwise (tested) |
| 4 | No per-account lockout; login timing revealed registered emails | Password guessing, account enumeration | Progressive lockout, per-email limits, constant-work verification for unknown emails |
| 5 | No CSRF protection (became necessary with cookies) | Cross-site state changes | Per-session synchronizer token (`X-CSRF-Token`) + Origin check |
| 6 | Reset link printed to the console in production when SMTP was missing; reset didn't end other sessions | Links leak to logs; attacker sessions survive reset | Printing only in development; reset revokes all sessions; links single-use |
| 7 | SSRF check resolved DNS, then the HTTP client resolved again (DNS rebinding); environment proxies honoured | Reach internal services / cloud metadata | Connected-peer IP is verified at socket level; proxies ignored unless `FETCH_PROXY` |
| 8 | No security headers | Clickjacking, MIME sniffing, weaker XSS defence | CSP (no inline scripts), HSTS, frame/nosniff/referrer/permissions/COOP headers, `no-store` API caching |
| 9 | Uploads trusted by extension; no zip-bomb or page-count limits | Memory exhaustion via crafted DOCX/PDF; disguised files | Magic-byte detection, executable/script refusal, DOCX expansion limit, PDF page cap, image pixel cap |
| 10 | Production started with missing `SECRET_KEY`, debug mode, wildcard CORS | Insecure-by-accident deployments | Startup validation per `APP_ENV`; production refuses unsafe settings |

### Medium
- **Data integrity:** schema changes were ad-hoc `ALTER`s with no version tracking; MySQL `TEXT`/`BLOB` (64 KB) would
  truncate or reject agent state, reports and images. *Fixed:* versioned, idempotent migrations (`schema_migrations`),
  `LONGTEXT`/`LONGBLOB` on MySQL, composite indexes, SQLite WAL + busy timeout.
- Deleting a conversation left approvals behind, and an admin reply to a deleted conversation created orphan messages.
  *Fixed:* cascading clean-up; reply delivery checks ownership/existence.
- Approvals never expired and two tabs could approve the same action. *Fixed:* expiry + atomic single-use claim.
- Stop/disconnect didn't stop the server-side model or tool work, and a dropped stream lost the partial reply.
  *Fixed:* real cancellation, partial replies saved and marked, heartbeats so proxies don't cut long agent runs.
  (Two client bugs found by the new end-to-end tests were also fixed: Stop didn't abort an active stream, and a
  just-sent message had only a temporary id, so it couldn't be edited until reload.)
- No token/cost tracking, quotas, budget, or concurrency limits. *Fixed.*
- Older messages silently dropped beyond `MAX_HISTORY_MESSAGES`. *Fixed:* rolling summary.
- Health was a single endpoint. *Fixed:* liveness + readiness.
- No backups/restore, no structured logs, no audit trail, no alerts. *Fixed.*
- Settings lived only in `localStorage` (didn't follow the user). *Fixed:* synced through the backend.
- Missing features: email verification, MFA, change password, sessions, export/delete account, conversation
  rename/pin/archive/search/export, message edit, timestamps, upload progress, admin pagination/search/audit/usage.
  *All implemented.*
- No automated tests, CI, Docker, or deployment docs. *Added.*

### Low
- iOS zoomed into the 15 px message box; drawer/dialogs had no focus trap; tooltips caused horizontal overflow on
  tablet/desktop widths; admin stat cards truncated at 320 px. *Fixed and covered by end-to-end checks.*

## 2. What was implemented (by requirement area)

1. **Completeness:** every button wired to a real endpoint; loading, empty, error and success states (toasts only after
   the server confirms); recoverable errors with Retry; offline banner with automatic recovery.
2. **AuthN/AuthZ:** see SECURITY.md. Cookie sessions, CSRF, lockout, MFA (TOTP + recovery codes), verification, reset,
   RBAC, resource-level checks on every route, audit log.
3. **Agent:** explicit step state (`running/awaiting/done/error/declined`, durations), bounded steps, per-tool timeouts,
   run deadline, cancellation, provider retries with exponential backoff (OpenAI SDK), approval expiry, permission
   re-check on resume, untrusted-content fencing, summarisation, memory controls, conversation search/export/delete,
   usage/cost tracking, quotas and budget, clear fallbacks when the provider is down or unconfigured.
4. **RAG:** OCR (scanned PDFs, images), hybrid BM25 + embeddings (RRF), re-indexing, duplicate detection, versioning,
   owner checks at upload/retrieval/answer time, safe deletion of indexed chunks, injection fencing.
5. **Chat UI:** streaming, typing indicator, timestamps, copy/regenerate/edit/retry/stop, rename/pin/archive/delete/export,
   search, multiple image attachments with progress and validation, voice, safe Markdown, tool progress, approvals.
6. **Admin:** tabs for overview, users (search/filter/paginate, role, disable, unlock, sign out), support requests,
   usage (tokens, cost, latency p50/p95, failures, models, top users, tool success), audit log, errors, evaluation.
7. **Cross-device:** responsive from 320 px (portrait and landscape), compact header overflow menu, drawer navigation,
   44 px touch targets, 16 px inputs, safe-area insets, focus traps, keyboard support, screen-reader labels, settings sync.
8. **Connectivity:** env-based config with validation, strict CORS, timeouts and retries (client and server), consistent
   JSON errors with codes, CSRF refresh, session-expiry handling, SSE heartbeats/idle detection, readiness checks,
   Redis-backed limiter, persistent volumes, "never report success unless the backend confirms".
9. **API/data security:** explicit request schemas (`nova/validation.py`), size/type/count limits, quotas, parameterised
   queries + indexes, SSRF egress controls, `.env.example` with placeholders only, production debug guard, headers,
   retention, export and deletion.
10. **Tests:** pytest, Vitest + Testing Library, Playwright (see results below).
11. **Deployment:** Dockerfile, docker-compose (Redis, optional MySQL), gunicorn config, GitHub Actions CI, README,
    DEPLOYMENT.md (env vars, migrations, first admin, health checks, backups, logging, rollback, troubleshooting).

## 3. Test results (executed in this environment)

| Suite | Command | Result |
|---|---|---|
| Backend (pytest) | `cd backend && pytest -q` | **126 passed** |
| Backend lint | `ruff check .` | **passed** |
| Frontend unit/component (Vitest) | `npm test` | **24 passed** |
| Frontend lint | `npm run lint` | **passed** |
| Frontend production build | `npm run build` | **passed** |
| End-to-end (Playwright, real backend + fake AI provider) | `npm run test:e2e` | **170 passed**, 4 skipped by design (touch-target and 16 px-input checks apply only to touch/phone projects), 0 failed — 29 scenarios × 6 device projects |
| Production profile under gunicorn | manual: `APP_ENV=production gunicorn …` | ready check, headers, JSON logs, CLI admin creation, unsafe-config refusal verified |
| docker-compose config | `docker compose config` | **valid** |

E2E projects run here: desktop Chromium 1280×800, 320×568 phone, Android Pixel 393×851, iPhone 390×844 portrait,
phone landscape 844×390, iPad 820×1180 (Chromium engine with mobile emulation). OCR tests ran with a real tesseract;
the shared-limiter test ran against a real redis-server.

**Not run here (and why):**
- **Firefox, WebKit/Safari and Edge** engines: only Chromium is installed in this sandbox. They're configured in
  `playwright.config.js` (`E2E_ALL_BROWSERS=1`) and run in CI. Real iOS/Android devices weren't available; mobile
  coverage is Chromium device emulation.
- **Docker image build:** Docker Hub is blocked from this sandbox. The compose file validates, and the exact
  production runtime (gunicorn, `APP_ENV=production`, `/data` SQLite) was run directly. CI builds the image and checks
  that the container becomes healthy.
- **MySQL:** no MySQL server here. The schema uses MySQL-safe types (`LONGTEXT`/`LONGBLOB`, sized `VARCHAR`s) and
  SQLAlchemy Core, but run the test suite against MySQL before relying on it (set `DATABASE_URL` in `tests/conftest.py`).
- **A real AI provider, SMTP server, web search and voice:** no credentials/network here. Provider calls were tested
  through the real OpenAI SDK against an OpenAI-compatible test server (streaming, tool calls, usage, errors, timeouts,
  embeddings). Voice uses browser APIs and needs a manual check on devices.

## 4. Remaining issues and recommendations (prioritised)
1. **Provide production credentials:** `LLM_API_KEY` (+ `LLM_BASE_URL` for non-OpenAI), `SECRET_KEY`, SMTP
   (`SMTP_HOST/USER/PASSWORD/FROM`) for verification and reset emails, optional `REDIS_URL`, `EMBEDDING_MODEL`,
   `TAVILY_API_KEY`/`BRAVE_API_KEY` for search, and per-token prices for cost estimates.
2. **Run CI once on GitHub** to execute the Firefox/WebKit/Edge e2e projects and the Docker build that couldn't run here.
3. **Account lockout can be abused** to keep a known user locked out (inherent trade-off). Password reset and admin
   unlock clear it. Add a CAPTCHA or proof-of-work on repeated failures if you see abuse.
4. **Timed-out tools keep running** in their worker thread until they finish (their result is discarded); tools
   already have their own network timeouts. Process-level isolation would be needed for hard kills.
5. **Migrations** are a lightweight built-in system (additive + recorded data migrations). If you expect
   destructive schema changes (renames/drops), adopt Alembic.
6. **Semantic search** computes similarity in Python over the selected documents' chunks, fine for thousands of chunks
   per user. For much larger corpora use a vector index (pgvector, Qdrant…).
7. **MFA enrolment** shows a setup key and an `otpauth://` link (opens authenticator apps on phones). A QR code would
   need an extra library; add one if desktop-to-phone enrolment is common.
8. **Conversation list** loads up to 200 chats (plus search). Add infinite scroll if users keep thousands.
9. Images are stored in the database (fine for moderate use). For heavy image use, move them to object storage.

## 5. Change list
**Backend:** `app.py` is now a thin entry point. The application lives in `backend/nova/`: `config.py` (env config +
validation), `db.py` (schema, engine, migrations), `security.py` (sessions, CSRF, limiter, TOTP, encryption, audit,
headers), `auth_routes.py`, `account_routes.py`, `conversation_routes.py`, `chat_routes.py`, `data_routes.py`,
`admin_routes.py`, `chat.py`, `agent.py`, `llm.py`, `rag.py`, `tools.py` (moved from `backend/tools.py`), `mail.py`,
`monitor.py`, `maintenance.py`, `backup.py`, `cli.py`, `users.py`, `validation.py`, `errors.py`. New: `gunicorn.conf.py`,
`requirements-dev.txt`, `ruff.toml`, `tests/` (7 test modules, fake provider, e2e server). Updated: `.env.example`,
`requirements.txt`, `setup_env.py`.

**Frontend:** new `AccountPanel.jsx`, `lib/settings.jsx`, `lib/toast.jsx`, `public/theme-init.js`, tests in
`src/**/__tests__`, `e2e/` (Playwright), `eslint.config.js`, `playwright.config.js`. Rewritten/updated: `App.jsx`,
`lib/api.js`, `lib/hooks.js`, `lib/theme.js`, `lib/voice.jsx`, all hooks, `AuthScreen`, `AdminDashboard`, `Sidebar`,
`ChatWindow`, `ChatMessage`, `ChatInput`, `DocumentsPanel`, `MemoryPanel`, `ConfirmModal`, `ThemeToggle`, `Tooltip`,
`IconButton`, `EvalPanel`, `index.html`, `vite.config.js`, `package.json`.

**Root:** `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.github/workflows/ci.yml`, `README.md`, `DEPLOYMENT.md`,
`SECURITY.md`, `AUDIT.md`, `.gitignore`.

**New dependencies:** backend runtime `cryptography`, `Pillow` (optional: `redis`, `pymysql`, `pytesseract`,
`pypdfium2`, `ddgs`); backend dev `pytest`, `ruff`. Frontend dev only: `vitest`, `jsdom`, `@testing-library/*`,
`@playwright/test`, `eslint` (+ React plugins). No frontend runtime dependencies were added, and existing ones weren't upgraded.

**New environment variables:** documented in `backend/.env.example`. Key additions: `APP_ENV`, `TRUSTED_PROXY_COUNT`,
`COOKIE_SAMESITE`, `COOKIE_SECURE`, `ALLOW_INSECURE_HTTP`, `SESSION_DAYS`, `SESSION_IDLE_HOURS`, `REGISTRATION_OPEN`,
`BOOTSTRAP_FIRST_ADMIN`, `REQUIRE_EMAIL_VERIFICATION`, `LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_MINUTES`, `MFA_ISSUER`,
`DATA_ENCRYPTION_KEY`, `SMTP_TLS`, `REDIS_URL`, `AUTH_/REGISTER_/UPLOAD_/API_RATE_LIMIT…`,
`MAX_CONCURRENT_STREAMS_PER_USER`, `DAILY_TOKEN_LIMIT_PER_USER`, `MONTHLY_BUDGET_USD`, `LLM_TIMEOUT_SECONDS`,
`LLM_MAX_RETRIES`, `LLM_STREAM_USAGE`, `LLM_PRICE_INPUT_PER_1M`, `LLM_PRICE_OUTPUT_PER_1M`, `EMBEDDING_*`,
`SUMMARIZE_HISTORY`, `MAX_PDF_PAGES`, `OCR_ENABLED`, `OCR_MAX_PAGES`, `AGENT_TIMEOUT_SECONDS`, `TOOL_TIMEOUT_SECONDS`,
`APPROVAL_TTL_MINUTES`, `FETCH_PROXY`, `LOG_RETENTION_DAYS`, `CHAT_RETENTION_DAYS`, `LOG_FORMAT`, `LOG_LEVEL`,
`ALERT_WEBHOOK_URL`, `ALERT_EMAIL`, `ALERT_ERROR_THRESHOLD`, `HOST`.

**Migrations:** automatic on start (`flask --app app migrate` to run explicitly). New tables `sessions`, `audit_log`,
`usage`, `schema_migrations`; new columns on `users`, `conversations`, `messages`, `documents`, `doc_chunks`,
`tool_log`, `pending_actions`; data migrations mark existing users as verified and close approvals created before
expiry existed. Verified against a database created with the original schema (data preserved, re-run is a no-op).
