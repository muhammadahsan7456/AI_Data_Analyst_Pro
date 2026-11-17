# Migration recovery progress

## Phase 1 — Login diagnosis

**Status:** Complete; diagnosis only. No application source changes have been made.

### Confirmed evidence

- Created branch `fix/post-migration`. The worktree already contained uncommitted changes in `.env.example`, `auth/routes.py`, `docker-compose.yml`, `static/css/landing.css`, and two auth templates; these are preserved and were not staged.
- Read-only connection using the local `.env` reached the SQL Server database whose name matches the configured `DB_NAME`. It is not empty: 41 `Users` rows. All 41 hashes have a bcrypt prefix and the expected 60-character length; `PasswordHash` is declared with a 500-character capacity. The app verifies the untrimmed UTF-8 password bytes with `bcrypt.checkpw`, matching the hash path; no evidence of hash truncation or policy mismatch was found.
- All 41 rows are `IsActive=1`; 28 are verified. There is one active, verified `SuperAdmin`. No account was currently locked; two accounts had nonzero failed-attempt counts (maximum 2). SQL Server collation is case-insensitive, and the database and app host both report UTC+05:00, so the observed username case and clock settings do not explain the failures.
- Subscription states include 31 `suspended` rows, but those rows remain `IsActive=1`. The auth decorator allows subscription states through; it blocks only a disabled account. No evidence that this state alone prevents login.
- Current local `.env` has a 64-character `SECRET_KEY`; `FORCE_SECURE_COOKIES` is absent, so the code default is `false`. No cookie domain is set. `run.py` configures one trusted loopback proxy hop for forwarded host/protocol/IP. These local settings do not reproduce the reported cookie failure, but production settings/process topology still need confirmation.
- SMTP settings are present locally and use the Brevo host category with port 587. SMTP login and sender are configured separately and differ, which is valid for Brevo only if the sender is verified in its provider account. Provider-side sender verification, quota, actual delivery, and the new server's logs could not be checked. Workspace logs contain no SMTP-related entries; `app_production.log` and `system.log` are empty.
- **Confirmed code-level OTP failure mode:** `EmailService._send_email()` reports success when SMTP credentials are absent, without sending mail. The admin path is synchronous, but this false success lets login proceed to OTP entry without delivering a code. Local credentials are present, so this is a code defect and a production-config-dependent cause, not proof that the current local SMTP setup failed.
- **Confirmed code-level session risk:** `run.py` generates a random secret when `SECRET_KEY` is missing. That invalidates sessions on restart and causes cross-process failures when processes do not share the same configured key. The local `.env` has a key; production stability was not independently verified.
- **Startup migration risk:** `init_db()` issues data-changing `UPDATE Users` statements on every startup: non-admin `active` rows with no end date become `free`; missing end dates for `active`/`expiring_soon` rows are backfilled; null statuses become `free`. The current database has no non-admin `active`/null-end-date rows, so no impact was observed in this snapshot. These unconditional repairs can nevertheless rewrite migrated legacy account state and need a conservative, auditable fix in Phase 2.
- `DATASET_ENCRYPTION_KEY` is configured locally and has a Fernet-shaped length. Its match to the key used for existing production ciphertext cannot be established from this checkout; dataset decryptability is separate from authentication.
- `.env.example` is already modified by the user. A name-only cross-check found code variables not documented there (`MAIL_FROM`, `PUBLIC_BASE_URL`, `SMTP_HOST`, `SMTP_USERNAME`; plus test-only `HEADLESS` and `TEST_BASE_URL`). Do not overwrite the existing changes.

### Remaining uncertainty

- The local DB target matches its local `DB_NAME`, and contains users, but this alone cannot prove it is the intended production database or that every specific historical account was migrated.
- No production SMTP/server logs or provider console are available here. Do not claim that a real OTP was delivered or identify an SMTP rejection without those.
- The production `SECRET_KEY`, cookie/proxy settings, process count, encryption-key continuity, and actual database target must be compared with the deployment configuration.

## Phase 2 — Login and admin access fixes

**Status:** Complete; code changes and isolated regression tests are in place.

- SMTP now fails closed when credentials are missing or a provider rejects delivery, and logs sanitized server-side failure details rather than reporting false success.
- Unverified-user login advances to code entry only after a successful send; the resend path now issues a six-digit OTP, rate-limits requests, and establishes the matching pending session. The login page's resend control targets that flow. OTP values are no longer written to audit logs.
- Malformed lockout timestamps are logged and fail closed for both user and admin sign-in.
- `SECRET_KEY` is required before database startup; no random per-process replacement is generated.
- The subscription data repair is guarded by a SQL Server migration marker so it runs once per database, not on every restart. Database initialization failures are no longer cached or swallowed, and the app does not continue serving after startup initialization fails.
- Added isolated tests for missing SMTP credentials, SMTP rejection, signup and email OTP verification, unverified login/resend, forgot-password request, admin OTP, wrong passwords, malformed lockout state, and failed DB initialization. Targeted result: **11 passed**. These tests use fakes; no real email was sent and no database rows were changed.

### Next

Phase 3: continue isolated QA. Do not start the UI redesign until the authenticated flows and interaction coverage are green.

## Phase 3 — Full QA sweep

**Status:** In progress; core targeted checks pass, but full authenticated/browser QA is incomplete. Phase 4 is gated.

### Completed

- Fixed an SQLite schema gap: existing `Users` tables now receive missing subscription columns during initialization. A regression test verifies the columns are added without losing the existing user row.
- Found a second OTP resend failure mode: database errors during rate-limit checks or token persistence were silently ignored, allowing sends to bypass the rate limit or present a code that could not be verified. Resend now fails closed, persists the replacement token and invalidates the old token in one transaction before sending, and logs persistence failures. Added tests for normal resend, failed rate-limit lookup, and failed token persistence.
- Replaced silent catches for dashboard metric/notification queries and related auth side effects with server-side logging. Malformed notification metadata now logs a warning rather than silently disappearing.
- Targeted isolated auth/email/database tests: **15 passed**. Application Python compile check: **passed**.
- With the single-process isolated QA server and temporary SQLite database, GET smoke tests returned HTTP 200 for public/auth pages (`/`, login/register/signup, forgot password, demo, features, pricing, testimonials, docs, FAQ, about, help, contact, privacy, terms, security overview), health/sitemap/robots, and admin login. Gated dashboard/upload/chat/admin pages redirected to the appropriate login page.
- In the browser, a wrong-password login showed the safe generic error; test registration reached the six-digit OTP screen; submitting a wrong OTP stayed on that screen and reported four attempts remaining. Homepage reload had no browser console or page errors. SMTP was mocked; no real email was sent.

### Incomplete / not verified

- The existing Selenium suite stalled (the full run stopped after 13 tests; a targeted run also hung). It was stopped rather than reported as passing. The browser request-context link-crawl helper also failed with a browser-protocol `Storage.getCookies` error, so link checks used direct HTTP smoke requests instead.
- The isolated browser session did not complete OTP verification, signed-in dashboard access, upload size/payment gates, Ask AI/chart generation, exports, payment approval/rejection, profile/company/logo, review/audit/settings interactions, or admin operational flows.
- Mobile widths, theme contrast, and the redesigned UI have not been tested; the redesign has not started because Phase 3 is not green.
- Production email delivery, proxy/cookie behavior, production logs, production database target, old production ciphertext compatibility, and live AI/payment integrations remain unverified. Do not use the local configured SQL Server for QA writes.

### Next

Resolve the Selenium/browser-test hang or replace it with a reliable isolated test path. Complete the authenticated flow and interaction checks using only temporary test data and mocked external services; then run the UI redesign in its required order and re-test each page group.
