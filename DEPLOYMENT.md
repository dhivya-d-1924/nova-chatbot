# Deploying Nova

## 1. Choose a topology

**Single server (recommended):** Flask serves the built frontend and the API from one origin. Cookies are first-party,
no CORS is needed, and `COOKIE_SAMESITE=Lax` works.

**Split hosting:** frontend on a static host (Netlify, Vercel, Cloudflare Pages), API elsewhere. Build the frontend with
`VITE_API_URL=https://api.example.com`, set `ALLOWED_ORIGINS=https://app.example.com` and `APP_URL=https://app.example.com`
on the backend. If the two are on different *sites* (not just subdomains of one domain), set `COOKIE_SAMESITE=None`
(requires HTTPS). Safari blocks third-party cookies, so prefer subdomains of one domain (`app.example.com` +
`api.example.com`) or the single-server setup.

## 2. Production settings (`backend/.env`)
Start from `backend/.env.example`. Minimum for production:

```env
APP_ENV=production
SECRET_KEY=<64 random hex characters>      # python -c "import secrets;print(secrets.token_hex(32))"
APP_URL=https://chat.example.com
ALLOWED_ORIGINS=https://chat.example.com
LLM_API_KEY=<provider key>
LLM_MODEL=gpt-4o-mini
DATABASE_URL=sqlite:////data/chat.db      # or mysql+pymysql://nova:<password>@db:3306/nova
REDIS_URL=redis://redis:6379/0             # required for correct limits with more than one worker
TRUSTED_PROXY_COUNT=1                      # one reverse proxy (nginx/Caddy/load balancer) in front
SMTP_HOST=smtp.example.com                 # + SMTP_USER/SMTP_PASSWORD/SMTP_FROM; enables email verification
```

Validate before starting: `flask --app app check-config`. In production the app **refuses to start** when, for
example, `SECRET_KEY` is missing or weak, `APP_URL` isn't HTTPS, `ALLOWED_ORIGINS` contains `*`, debug is on, the
LLM key is still the placeholder, or `REDIS_URL` is set but unreachable. Warnings (no SMTP, several workers without
Redis…) are logged at startup. Keep secrets out of source control (`.env` is git-ignored); in the cloud prefer your
platform's secret manager and pass them as environment variables.

## 3. Run it

### Docker Compose (app + Redis, optional MySQL)
```bash
cp backend/.env.example backend/.env   # edit as above
docker compose up -d --build
docker compose exec app flask --app app create-admin --email you@example.com
```
The app listens on `127.0.0.1:8000`; put a TLS proxy in front (below). Data lives in the `nova-data` volume.
MySQL: `MYSQL_PASSWORD=… MYSQL_ROOT_PASSWORD=… docker compose --profile mysql up -d` and set
`DATABASE_URL=mysql+pymysql://nova:<password>@mysql:3306/nova`.

### Without Docker
```bash
cd frontend && npm ci && npm run build          # creates frontend/dist, served by Flask
cd ../backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt redis            # + pymysql for MySQL, + pytesseract pypdfium2 for OCR
flask --app app migrate
flask --app app create-admin --email you@example.com
gunicorn -c gunicorn.conf.py app:app             # binds 0.0.0.0:8000; WEB_CONCURRENCY workers × 8 threads
```
Run it under systemd (or similar) with `Restart=always`. The development server (`python app.py`) refuses to run
with `APP_ENV=production`.

### Reverse proxy and HTTPS
Caddy (automatic certificates):
```
chat.example.com {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1          # stream replies immediately
    }
}
```
nginx:
```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;            # required for streaming (the app also sends X-Accel-Buffering: no)
    proxy_read_timeout 300s;        # agent runs can take minutes; the app sends heartbeats every 15 s
    client_max_body_size 20m;       # >= MAX_UPLOAD_MB
}
```
Set `TRUSTED_PROXY_COUNT` to the number of proxies in front; otherwise `X-Forwarded-For` is ignored (it can be forged)
and every client appears to come from the proxy's address.

## 4. First administrator
In production nobody becomes admin by registering. Choose one:
- `flask --app app create-admin --email you@example.com` (prompts for a password; promotes an existing account), or
- `ADMIN_EMAILS=you@example.com` together with `REQUIRE_EMAIL_VERIFICATION=true` and working SMTP: the address
  becomes admin only after its owner clicks the verification link.

Admins can then manage roles in the dashboard. The app refuses to remove or disable the last active admin.

## 5. Health checks
| Endpoint | Use |
|---|---|
| `GET /api/health/live` | Liveness: the process answers. Use for container restarts. |
| `GET /api/health/ready` | Readiness: database reachable, schema current, Redis reachable (if configured). 503 otherwise. Use for load-balancer routing. |
| `GET /api/health` | Model name and whether a provider key is configured (used by the UI). |

## 6. Database migrations
Migrations run automatically on start and are safe to run from several workers; run them explicitly during a deploy
with `flask --app app migrate`. They are additive (new tables, columns and indexes plus recorded data migrations in
`schema_migrations`), so the previous app version keeps working against an upgraded database, which makes rollback
safe. Check the version in `GET /api/health/ready`.

## 7. Backups and restore
**SQLite** (consistent online backup + integrity check):
```bash
flask --app app backup                       # -> backend/backups/chat-YYYYmmdd-HHMMSS.db (verified)
flask --app app backup /mnt/backups/         # or a directory/file of your choice
```
Schedule it (cron/systemd timer) and copy backups off the server. Restore (stop the app first):
```bash
flask --app app restore /mnt/backups/chat-20261014-020000.db --yes
```
The current file is kept as `chat.before-restore-*.db`, and the backup is integrity-checked before and after
copying. Backup and restore are covered by automated tests (`tests/test_ops.py`). **Test a restore regularly** on a
copy of production.

**MySQL:**
```bash
mysqldump --single-transaction --routines --triggers -u nova -p nova | gzip > nova-$(date +%F).sql.gz
gunzip -c nova-2026-10-14.sql.gz | mysql -u nova -p nova      # restore into an empty database
```
In Docker: `docker compose exec app flask --app app backup /data/backups/` and back up the `nova-data` volume.

## 8. Logging, monitoring and alerts
- Logs go to stdout. `LOG_FORMAT=json` (default in production) emits one JSON object per line with `request_id`,
  method, path, status, duration and user id. Every response carries `X-Request-Id`.
- Security events (sign-ins, failures, lockouts, password and MFA changes, role changes, approvals, exports, account
  deletion) are written to the audit log, visible in **Admin → Audit log**. Passwords, tokens and message contents are
  never logged.
- AI usage (tokens, estimated cost, latency, failures) is in **Admin → Usage**; set `LLM_PRICE_INPUT_PER_1M` and
  `LLM_PRICE_OUTPUT_PER_1M` for cost estimates and `MONTHLY_BUDGET_USD` / `DAILY_TOKEN_LIMIT_PER_USER` for limits.
- Alerts: set `ALERT_WEBHOOK_URL` (Slack-compatible `{"text": …}`) and/or `ALERT_EMAIL`; you're notified when errors
  in 10 minutes exceed `ALERT_ERROR_THRESHOLD` (at most every 30 minutes per worker).
- Retention: logs older than `LOG_RETENTION_DAYS` (90) are deleted hourly; set `CHAT_RETENTION_DAYS` to delete
  unpinned chats idle that long. Run `flask --app app maintenance` to clean up immediately.

## 9. Rolling out and rolling back
1. Back up the database (section 7).
2. Deploy the new version; migrations run on start. Watch `/api/health/ready` and the logs.
3. To roll back, deploy the previous version (schema changes are additive, so it runs against the upgraded
   database). Restore the backup only if data itself must be reverted.
Users stay signed in across deploys (sessions are in the database). Rotating `SECRET_KEY` invalidates outstanding
reset/verification links only; rotating `DATA_ENCRYPTION_KEY` (defaults to `SECRET_KEY`) disables existing MFA
enrolments, so set `DATA_ENCRYPTION_KEY` separately before you ever rotate `SECRET_KEY`.

## 10. Scaling notes
- Several workers/servers need `REDIS_URL` (shared rate limits, quotas, concurrency) and MySQL (or a shared
  database); SQLite is fine for one server.
- Streaming uses threads (`gthread`); raise `GUNICORN_THREADS` for many simultaneous replies.
- Uploaded files are not stored, only extracted text (documents) and images (database). Size limits:
  `MAX_UPLOAD_MB`, `MAX_IMAGE_MB`, `MAX_PDF_PAGES`.

## 11. Troubleshooting
| Symptom | Fix |
|---|---|
| App exits with "Invalid configuration" | Read the listed problems; `flask --app app check-config` shows them without starting. |
| "Your session token is missing or out of date" | Reload. Behind a proxy, check `APP_URL`/`ALLOWED_ORIGINS` match the browser's address exactly (scheme + host + port). |
| Sign-in works but you're immediately signed out (split hosting) | Cookies are blocked as third-party: use one site, or `COOKIE_SAMESITE=None` with HTTPS. |
| Replies appear all at once / hang | Disable proxy buffering (see nginx/Caddy config). With Agent tools on, replies arrive in one piece by design. |
| Everyone shares one rate limit | Set `TRUSTED_PROXY_COUNT` correctly. |
| Limits reset randomly with several workers | Set `REDIS_URL`. |
| "No AI provider is configured" | Set `LLM_API_KEY` (and `LLM_BASE_URL` for non-OpenAI) and restart. |
| Provider errors mentioning `stream_options` | Set `LLM_STREAM_USAGE=false`. |
| Scanned PDFs say "No readable text" | Install `tesseract-ocr` plus `pip install pytesseract pypdfium2`. |
| Password-reset emails don't arrive | Configure `SMTP_*`; in development the link is printed in the backend terminal. |
| Locked out of an account | An admin can **Unlock** it under Admin → Users, or reset the password by email. |
