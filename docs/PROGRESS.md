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

**Status:** Groups 1–8 and final non-Selenium regressions complete. Selenium remains unverified; external integrations and some authenticated settings remain out of scope. Phase 4 is unblocked.

### Completed

- Upload size group passed. In the isolated QA server, the free test account uploaded a 600,018-byte CSV successfully; an exactly 1 MiB request returned HTTP 400 with `upgrade_required=true`. The >500 MiB server-side guard returned HTTP 400 for a simulated stream reporting 500 MiB + 1 byte, without allocating a large file. Added focused regression tests for 1 MiB - 1 byte, exactly 1 MiB, and >500 MiB; **3 passed**.
- Ask AI group passed using five authenticated HTTP questions with a mocked analysis result (no live AI calls): region sales → bar, monthly trend → line, product ranking → horizontal bar, units/revenue relationship → scatter, and order status → pie. Each result had non-empty, length-matched labels/values and the chat HTML included its interactive chart container/spec.
- Root cause found while testing: SQLite table discovery used only SQL Server `INFORMATION_SCHEMA`, so uploaded SQLite datasets were incorrectly reported as missing in `/chat`. Added `sqlite_master` discovery and regression coverage.
- Chart generation also overrode suitable chart selections: grouped trends/regions were forced to pie charts, and two-numeric-column relationships became bar charts of unrelated averages. Specs now follow the chart selector for grouped results and use paired numeric values for scatter points. SQLite and chart regression tests: **8 passed**.
- Export group passed over authenticated HTTP from the isolated 120-row test dataset. CSV (**5,686 bytes**, expected headers and 120 data rows), Excel (**9,780 bytes**, valid ZIP with workbook XML), PDF (**115,462 bytes**, PDF signature/EOF), Word (**128,219 bytes**, valid ZIP with document XML), and PowerPoint (**136,163 bytes**, valid ZIP with presentation XML) all returned HTTP 200 with the expected MIME type and valid file structure.
- Fixed an SQLite schema gap: existing `Users` tables now receive missing subscription columns during initialization. A regression test verifies the columns are added without losing the existing user row.
- Found a second OTP resend failure mode: database errors during rate-limit checks or token persistence were silently ignored, allowing sends to bypass the rate limit or present a code that could not be verified. Resend now fails closed, persists the replacement token and invalidates the old token in one transaction before sending, and logs persistence failures. Added tests for normal resend, failed rate-limit lookup, and failed token persistence.
- Replaced silent catches for dashboard metric/notification queries and related auth side effects with server-side logging. Malformed notification metadata now logs a warning rather than silently disappearing.
- Targeted isolated auth/email/database tests: **15 passed**. Application Python compile check: **passed**.
- With the single-process isolated QA server and temporary SQLite database, GET smoke tests returned HTTP 200 for public/auth pages (`/`, login/register/signup, forgot password, demo, features, pricing, testimonials, docs, FAQ, about, help, contact, privacy, terms, security overview), health/sitemap/robots, and admin login. Gated dashboard/upload/chat/admin pages redirected to the appropriate login page.
- In the browser, a wrong-password login showed the safe generic error; test registration reached the six-digit OTP screen; submitting a wrong OTP stayed on that screen and reported four attempts remaining. Homepage reload had no browser console or page errors. SMTP was mocked; no real email was sent.
- Payment group passed using an isolated SQLite-backed HTTP server with CSRF enabled and email delivery mocked. Submitted EasyPaisa, NayaPay, and SadaPay proof screenshots for separate QA accounts; all three canonical provider values appeared in the pricing form and the admin payment list. Super Admin password + mocked six-digit OTP sign-in succeeded; the paginated admin list showed all three transaction IDs. EasyPaisa and SadaPay approvals persisted `Completed` and activated subscriptions; NayaPay rejection persisted `Rejected` with the supplied review reason. No real payment or email was used.
- Fixed payment-flow defects exposed by that test: pricing JavaScript now submits its CSRF token and reports network/JSON failures as failures; provider names are normalized and unsupported methods rejected; SQLite now has payment review columns and `SiteAnnouncement`; its adapter translates SQL Server `OFFSET/FETCH` pagination and casted-date `DATEADD` while preserving month-end clamping. Admin approval now formats SQLite's date-string result as a date before displaying the subscription end date.
- Added SQLite adapter/schema regression tests, including legacy payment-column migration, pagination execution, and Jan-31-to-Feb-29 month addition. Targeted database tests: **6 passed**. Admin dashboard still logs a SQLite `near "1"` query error after OTP sign-in; investigate in the next admin-pages group.
- Admin/login/pages group passed through the isolated HTTP server. Super Admin password + mocked OTP sign-in succeeded; `/admin/dashboard`, `/admin/users`, `/admin/payments`, `/admin/audit-logs`, `/admin/settings`, `/admin/subscribers`, `/admin/contact-messages`, and `/admin/reviews` all returned HTTP 200. Review create, hide/show (confirmed on `/testimonials`), edit, and delete all persisted correctly.
- Fixed the dashboard and users-page SQL failures: SQLite translation now handles nested `TOP 1`, `DATEADD` with bound date parameters, and SQL Server `FORMAT(..., 'yyyy-MM')`; the user search query uses a correlated scalar subquery instead of SQL Server-only `OUTER APPLY`. Added SQLite `PaymentSettings` schema/defaults with blank account details for isolated QA.
- Review creation previously attempted its audit-log insert before the review transaction committed, causing SQLite `database is locked` warnings. The audit write now occurs after the primary transaction commits. Database/query tests: **8 passed** (Python 3.12 reports sqlite datetime-adapter deprecation warnings; assertions pass).
- Profile/public-page group passed over authenticated and anonymous HTTP sessions. `/auth/profile` saved a company name on a new profile and updated it on a second save; a PNG company logo was stored, rendered, and fetched with HTTP 200. `/docs`, `/faq`, `/about`, and `/help` all returned HTTP 200 and rendered the public landing shell anonymously and the dashboard sidebar for the logged-in user.
- Fixed the profile insert/update blocker: the previous `IF EXISTS ... BEGIN/END` T-SQL upsert was invalid in SQLite. The profile route now performs a portable existence check and calls separate parameterized insert/update queries. Added a regression test for both query paths; targeted database tests: **9 passed**.
- Theme/responsive group passed in Playwright on the isolated server. Login, signup, OTP verification, and forgot-password pages had no horizontal overflow at 360, 390, or 768 px in either theme. Dashboard also had no overflow at those widths; its mobile sidebar opened and closed through the backdrop at each width. Theme buttons changed and persisted the selected theme. Representative dashboard text and search-placeholder contrast measured at least **7.24:1**; auth form placeholder contrast measured at least **6.84:1** in dark mode and **9.9:1** in light mode.
- Fixed auth form placeholder contrast: the landing shell did not define placeholder colors, so browsers used low-contrast defaults. Auth placeholders now use the existing theme variables with full opacity, and the landing stylesheet URL version was bumped so clients receive the fix. Rechecked the served v2 stylesheet and all auth pages; no application page errors were seen in the dashboard/auth navigation smoke.
- Internal-link group passed in Playwright against the isolated server. Logged-in and anonymous crawls visited **28** and **25** unique internal paths respectively; no 404, 5xx, or navigation failures occurred. The unauthenticated OTP route redirected to login as expected, and gated routes redirected to the dashboard/login. A VS Code BrowserView preload exception appeared while rendering `robots.txt`; it was from the host browser preload, not application code.
- Final regression passed: **30 non-Selenium tests passed** across upload limits, email service, database initialization, chart generation, and auth/email flows. `compileall` passed. An accidental `pytest tests -q` invocation also collected Selenium-based files; all 7 failures were `ERR_CONNECTION_REFUSED` against the suite's default `127.0.0.1:5000` (the isolated app runs on `5051`). That Selenium invocation is not being repeated or treated as an application regression.

### Incomplete / not verified

- Selenium-based browser tests remain unverified; a prior run stalled, and the accidental broad pytest invocation targeted the default port rather than the isolated QA server. A browser request-context helper had a `Storage.getCookies` protocol error, but the replacement Playwright page-navigation crawl completed successfully.
- The remaining authenticated user forms and other profile/security settings were not included in the profile/public-page group. Ask AI was exercised with deterministic mocks only; real provider responses/model behavior remain unverified.
- Production email delivery, proxy/cookie behavior, production logs, production database target, old production ciphertext compatibility, and live AI/payment integrations remain unverified. Do not use the local configured SQL Server for QA writes.

## Phase 4 — UI redesign

**Status:** In progress; groups 1–5 complete, group 6 in progress.

### Group 1 — Shared tokens and typography

- Updated the dashboard and public/auth palettes to warm neutrals, ink, and copper while preserving existing CSS variable names. Replaced the dashboard glass-card blur with solid theme surfaces, applied the monospace token and tabular figures to dashboard metrics and landing stats, and updated both stylesheet cache versions.
- Added `prefers-reduced-motion` rules to both shared stylesheets. Playwright emulation confirmed smooth scrolling is disabled and animation/transition durations are reduced to `0.01ms` on auth and dashboard pages.
- Checked the public home page in light and dark themes at 360, 390, 768, and 1440 px; auth at 360, 390, and 768 px; and dashboard at 360, 390, 768, and 1440 px. No horizontal overflow was observed. Dashboard metrics use the monospace font. Auth placeholder contrast measured at least **7.21:1** in light mode and **5.77:1** in dark mode.
- Browser QA used the isolated temporary SQLite-backed server and a disposable QA account; it did not write to the configured SQL Server database or send email.

### Next

### Group 2 — Public header, footer, and mobile navigation

- Reworked the public header/footer with theme variables, clearer focus states, and a compact mobile drawer. Added mobile drawer entries for Register and Dashboard while hiding the wide header CTAs at mobile widths, so those destinations remain accessible without clipping.
- Playwright checked the anonymous public shell at 360, 390, 768, 1024, and 1440 px in both themes. No document/header overflow or overlap was detected. Footer description/link contrast measured **9.42:1** in light mode and **11.17:1** in dark mode.
- Verified the mobile drawer opens with synchronized `aria-expanded`, overlay, and body-lock state; Escape closes it and restores focus; clicking the overlay closes it; and the drawer Register link loads `/auth/register`. No page errors were observed during these checks.
- Authenticated header rendering was not rechecked in this group; the browser QA session was anonymous. The existing dashboard destination remains available through the added authenticated drawer item.

### Next

### Group 3 — Home page hero and sections

- Replaced the generic gradient-led hero with a question-first analyst workspace, an inline SVG sample revenue chart and sparkline, compact monospace KPI figures, and a three-step workflow section. Reworked the final CTA to use the theme surfaces and concrete copy; preserved the existing route targets, counter hooks, typed-text ID, and video modal behavior.
- Playwright verified HTTP 200, no horizontal overflow or off-screen hero/workflow/CTA elements at 360, 390, 768, 1024, and 1440 px in both themes, and no page or console errors. Hero heading/badge contrast measured **5.78:1** in light mode and **6.59:1** in dark mode; primary CTA text measured **13.29:1** and **15.57:1** respectively.
- The new typed-text cursor and preview transition reduce to **0.01 ms** under `prefers-reduced-motion`. The sample chart renders with its accessible title/description; all four counter hooks and the existing hero CTA/video IDs remain present.
- The demo and registration CTAs returned HTTP 200. The video modal opened and closed correctly; its media request was deliberately aborted during QA to avoid downloading the large video. Authenticated home-page rendering was not rechecked.
- Kept the shared counters, reveals, and typed-text animation while skipping Three.js and vanilla-tilt downloads on the redesigned home page. Browser request inspection confirmed neither heavy CDN library was requested, the local animation engine initialized, and the typed example continued to render.

### Group 4 — Authentication pages

- Added a quieter, theme-aware form treatment with a subtle grid, defined borders, restrained focus states, and consistent control sizing. Updated OTP digit inputs, reset-password controls, and the OTP animation overlay; the overlay now defines its own theme accent because it renders outside the auth-page wrapper.
- Verified login, registration, forgot-password, and reset-password routes in Playwright at 360, 390, and 768 px in both light and dark themes (24 route/theme/viewport combinations). Cards and visible controls remained within the viewport, with no horizontal page overflow. Login password visibility toggle worked; no browser console or page errors occurred. The pages no longer load Three.js or vanilla-tilt.
- OTP requires a pending session and the unauthenticated route redirects to login. OTP styling is implemented, but was not rendered through the live route because creating the session would require entering the email/OTP flow; no email was sent during QA. Reset-password page itself rendered on the isolated server.
- Preserved the pre-existing signup template changes and unrelated OAuth stylesheet hunk; neither is part of this group.

### Group 5 — Dashboard shell and KPI cards

- Replaced the generic dashboard heading with a concrete workspace summary; added a low-contrast graph-paper canvas, a calmer sidebar active state, a cleaned-up smart-search panel, and six compact KPI cards with supporting descriptions. Payment and privacy notices now use theme variables rather than hardcoded gradients and colors.
- Kept existing dashboard metric classes and smart-search IDs, and added styling classes only. Jinja parsing passed for `dashboard.html` and `base.html`.
- Playwright checked the dashboard shell/KPI layout using a local fixture with the actual app stylesheet at 360, 390, 768, 1024, and 1440 px in both themes. There was no horizontal overflow; KPI layout was two columns through tablet and three on desktop. Text contrast measured 14.75:1/13.24:1 for KPI values, 4.95:1/6.50:1 for captions, and 4.89:1/5.82:1 for pending status (light/dark). No browser errors were observed.
- The protected `/dashboard` route correctly redirected to login in the isolated server because no authenticated QA account was available in this browser session. Therefore the live, data-backed dashboard was not rendered; no email was sent and no database records were changed.
- Preserved the pre-existing `.env.example`, `docker-compose.yml`, signup template, and OAuth stylesheet changes; they are outside these groups.

### Group 6 — Chat workspace

- Refined the question-first chat copy and added theme-aware styling for the dataset selector, prompt, suggestions, results, and chart controls. Removed the chart switcher's inline blue/purple gradient and replaced emoji labels with plain text; the existing IDs, class hooks, form names, handlers, and routes remain intact. Bumped the shared stylesheet cache version.
- The updated shared stylesheet returned HTTP 200 from the local QA server. A Playwright CSS fixture using that stylesheet passed no-overflow checks at 360, 390, 768, and 1440 px in both themes; active chart-control text contrast measured 4.95:1 in light mode and 5.61:1 in dark mode.
- The live `/chat` route redirected to sign-in, so the authenticated chat page and its actual voice, suggestion, chart-switch, and export interactions were not exercised in this group. Do not mark the group complete until those interactions can be tested in an isolated authenticated QA session.

### Next

Finish Group 6 interaction verification using an isolated authenticated Playwright session, then continue to Group 7.

---

# Release: final polish (branch `release/final-polish`)

Started 2026-11-18. `fix/post-migration` was already merged into `main` (merge commit `462617d`), so this branch starts from `main` and does not repeat that work. Environment used: Python 3.12 venv, SQLite QA fallback (`ALLOW_SQLITE_FALLBACK=true`, throwaway DB and throwaway Fernet key), unixODBC shared library only (no SQL Server available). Nothing here touched the production database, production email, or Docker files.

## R-Phase 1 — Audit and baseline

**Status:** Complete.

- Inventory: 125 URL rules. Public: `/`, `/features`, `/pricing`, `/testimonials`, `/faq`, `/docs`, `/about`, `/help`, `/contact`, `/demo`, `/privacy`, `/terms`, `/security-overview`, `/health`, `/sitemap.xml`, `/robots.txt`. Auth (`/auth/*`): login, register/signup (+details, payment), verify-otp, resend-otp, resend-verification, verify-email, forgot/reset password (+verify-code), google, google/login, google/callback, profile, settings, security, change-password, logout. Portal: `/dashboard`, `/upload`, `/api/upload-file`, `/api/upload-status/<job>`, `/chat`, `/dataset/*` (view, details, edit, clean, export, favorite, tags, insights-toggle, delete), `/predictions`, `/report-builder/<id>`, `/executive-summary/<id>`, `/data-health/<id>`, `/receipts*`, `/subscription/renew`, `/waiting-approval`, `/settings/enterprise`, `/query/export/<fmt>`, `/download-chart`, `/api/*` (notifications, history, alert rules, scheduled reports, smart search, insights, quality, health, multi-dataset). Admin (`/admin/*`): login, verify-otp, dashboard, users (+export, role, status, extend, delete), payments (+status, invoice, export), reviews, settings (+payment, backup, password), subscribers, contact-messages, audit-logs, logout.
- Baseline tests: 30 non-Selenium tests passed. Selenium suites (`test_auth.py`, `test_public_pages.py`, `test_demo_sandbox.py`) need a running server + Chrome and were not run.
- Suspected problem areas (to verify in later phases): Google OAuth (confirmed, R-Phase 2); admin console accepted a normal portal session for admin roles (OTP bypass, fixed); open redirect via `?next=` on login (fixed); `wsgi.py` bound 0.0.0.0 without proxy trust, so real client IP/HTTPS scheme were lost (fixed); state-changing GET `/dataset/delete/<id>`; production cookie lacks `Secure`, no HTTPS redirect on port 80, no HSTS (live check: config, not code); `PUBLIC_BASE_URL` unset in production makes email links default to `http://127.0.0.1:5000` (env).

## R-Phase 2 — Google sign-in

**Status:** Code fixed and covered by tests; live verification needs the deploy.

Live evidence gathered before changes (read-only requests to the public site):
- `https://aidataanalystpro.co.uk/auth/google` → 302 to Google with `redirect_uri=https://aidataanalystpro.co.uk/auth/google/callback`; Google shows its normal account chooser (no `redirect_uri_mismatch`), so the Cloud Console redirect URI is already accepted.
- `https://www.aidataanalystpro.co.uk/auth/google` sets the state cookie on the **www** host but sends Google the **apex** callback, so the state check always fails for www visitors.
- Session cookie is served without `Secure`; `http://` is served directly (no redirect to https).

Root causes in code (`auth/routes.py`):
1. New Google users always failed: `upsert_user_profile()` (25 markers) called with 11 params and `upsert_user_settings()` (13 markers) with 6 → driver error → transaction rolled back → "Google authentication error".
2. Existing users always failed on SQL Server: inline T-SQL inserted into non-existent columns `UserProfiles.CreatedAt`, `UserSettings.ExportFormat/DefaultChart/DefaultDashboard` (compile-time error even in an unexecuted branch).
3. www/apex host mismatch (above).
4. `email_verified` never checked; an unverified password account with the same email was merged and its unproven password kept working.
5. Raw exception text was flashed to users; token-exchange errors were `print`ed with Google's response body.

Fix: canonical origin from `PUBLIC_BASE_URL` (falls back to the apex domain, then the request); login hop to the canonical host before state is set; state + 10-minute expiry + PKCE (S256); `email_verified` required; portable row creation for `UserProfiles`/`UserSettings`; new users are `free`, verified, no OTP/payment step; existing verified users are linked and keep their password; unverified same-email accounts get verified and their unproven password retired (owner can use Forgot password); suspended users refused; payment-gate redirect same as password login; friendly messages, details only in server logs. Optional `GOOGLE_REDIRECT_URI` override.

Related security fixes: `admin/decorators.py` + `admin/routes.py` now require `admin_user_id` (set only by admin password + OTP), so a portal session (password or Google) for an admin no longer opens `/admin`. Login `?next=` accepts only same-site relative paths. `wsgi.py` binds 127.0.0.1 with one trusted local proxy hop outside Docker (Docker unchanged; `BIND_HOST` override).

Tests: `tests/test_google_oauth.py` (9 tests, real auth blueprint on a temp SQLite DB, Google endpoints mocked). Before the fix: 6 of 8 OAuth tests failed (new and existing users redirected back to `/auth/login`). After: 9 passed. Full non-Selenium suite: 39 passed.

Not verifiable here: a real Google round-trip and the SQL Server execution of the new statements (both need the deployed app).

## R-Phase 3 — Black-and-white design system

**Status:** Core pass committed; remaining items listed below.

- Tokens (`static/css/style.css`, `landing.css`, `admin/base_admin.html`) and a last-loaded `static/css/mono.css`: type scale, surfaces, buttons, forms, focus rings, tables, badges, alerts, nav, icon tiles, mobile drawer, `prefers-reduced-motion`, print. Light/dark via `data-theme` (`static/js/theme.js`).
- Self-hosted Inter and JetBrains Mono (`static/fonts/`, SIL OFL, `static/css/fonts.css`); Outfit/Space Grotesk aliased to Inter so no template change was needed.
- Gradients and decorative orbs removed; ~244 emoji removed from templates, Python and JS (Font Awesome icons in toasts, theme toggle, flashes).
- Charts monochrome: Plotly specs (`visualization/chart_generator.py`, `visualization/charts.py`), Chart.js defaults.
- Copy corrected: "AES-256" → Fernet (AES-128-CBC + HMAC-SHA256) in 17 files; homepage "10M+ rows / 256-bit / 97.9% uptime" stats replaced with verifiable facts (1 MB free, 500 MB Pro, 7 formats, 2 languages).
- `utils/encryption.py`: no longer generates a key and appends it to `.env` at runtime; a missing/invalid `DATASET_ENCRYPTION_KEY` disables encryption with an error log instead of producing ciphertext nobody can decrypt.
- Checks: `node --check` on every `static/js/*.js`; `compileall`; screenshots at 1440 and 360 for public, portal and admin pages showed no horizontal overflow.

Remaining: inline gradients in `auth/signup.html`, `pricing_page.html`, `features.html`; `auth/email_service.py` email colours; full re-audit of `dashboard.html` and `dataset_*`.

## R-Phase 6 — Ask AI accuracy and guardrails (started early)

QA battery (`execute_multi_concept_analysis`, the same call `/chat` makes) on a 6,000-row sales CSV, each answer compared with pandas on the source file. No OpenRouter key in QA, so only the deterministic layers ran.

| | Before | After |
|---|---|---|
| In-scope answers correct | 8 / 17 | 17 / 17 |
| Off-topic refused | 6 / 10 | 10 / 10 |
| Missing column / value reported honestly | 0 / 2 | 2 / 2 |

Root causes (`ai/gemini.py` unless noted):
1. When neither the pattern engine nor the LLM produced SQL, the code ran `SELECT TOP 50 *` and showed those rows as the answer. Same after failed repairs. Now a plain "couldn't match that question to your columns" message.
2. The LLM's own "unrelated to the dataset" verdict was replaced with `SELECT TOP 50 *`. Now it is a refusal.
3. "How many records are there?" returned 10 rows; pure count now checked before the plan engine.
4. "total shipping cost by region" silently summed `UnitPrice`. Now: "There is no 'shipping cost' column", with the real numeric columns listed.
5. Grouped comparisons ("compare profit of North and South") returned numbers without the group labels.
6. "monthly sales trend" returned the raw Sales column; now grouped by month of the date column.
7. Close misspellings of column names ("totl sales by regon") now corrected before planning.
8. Guardrail: Roman Urdu particles ("me", "se", "ka", "do") counted as data words, so most off-topic sentences passed; "who is the top customer" was refused. Rewritten: off-topic subjects (weather/mausam, crypto, movies, news, recipes…) refused unless a column is named; data questions accepted on column names, dataset values, or analytic words (incl. kitne, zyada, batao, dikhao).
9. `ai/schema_intelligence.py`: labels like "Customer 0369" were classified numeric (all non-digits stripped), so they were offered as metrics.
10. QA-only (`database/connection.py`, SQLite adapter): nested `SELECT TOP` subqueries, `TRY_CAST`, `NVARCHAR(MAX)`, `CAST(.. AS DATE)`, `YEAR()/MONTH()` were not translated, which had hidden bugs 1–9 behind SQL errors. Production SQL Server is unaffected by this adapter.

Server hard timeout: `/chat` already caps the pipeline at 25 s (`ASK_AI_TIMEOUT_SECONDS`) and returns a clear message.
Tests: `tests/test_ask_ai_guardrails.py` (30). Non-Selenium suite: 69 passed.
Not verified: the LLM path (needs `OPENROUTER_API_KEY`) and these queries on SQL Server.

## R-Phase 5 — Upload and encrypted storage

**Status:** Encryption at rest implemented and verified on the QA (SQLite) database; SQL Server execution not verified here.

Finding: before this change, new uploads were stored in plaintext. `database/connection.py` had encryption switched off "for Ask AI speed", while the site said data was encrypted at rest. Verified: a 6,000-row CSV produced a table whose text columns were readable with plain SQL.

Owner's requirement (2026-11-19): customer datasets must be encrypted so that even the Super Admin cannot read them, while Ask AI keeps answering normally.

Design (`utils/dataset_vault.py`):
- Each dataset gets its own random AES-256-GCM key, stored only wrapped by the server master key `DATASET_ENCRYPTION_KEY` (env, never in the DB).
- Rows are stored as encrypted Parquet chunks (≤100k rows each) in `DatasetVault`; `DatasetVaultMeta` holds the wrapped key and counts. Table name + chunk number are authenticated (AAD).
- The dataset's own SQL table is still created (schema, types, column lookups) but holds no rows.
- `run_query()` sends any SELECT on a vaulted table to an in-memory SQLite copy decrypted for that request (never written to disk), cached for 15 minutes and bounded by total rows. Prewarmed right after upload.
- Uploads are refused with a clear message if the master key is missing, instead of silently storing plaintext.
- Deleting a dataset (user or admin) removes its ciphertext and key.
- Existing plaintext datasets: `python -m utils.dataset_vault migrate` (idempotent; skips vaulted tables; empties the plaintext table after copying). Not run automatically.

Honest limit: someone holding BOTH the database and the server's `DATASET_ENCRYPTION_KEY` can decrypt (the server must read data to answer questions). The DB alone, backups and the admin console show only ciphertext. Column names are not encrypted.

Schema changes (additive, idempotent, created on first use): tables `DatasetVault(TableName, ChunkNo, Payload)` and `DatasetVaultMeta(TableName, WrappedKey, KeyVersion, TotalRows, ChunkCount, CreatedAt)`.

Also fixed: `uploads/data_loader.py` downcast float columns to float32 (~7 significant digits), so totals drifted (650k-row Profit total 252,743,264 vs true 252,743,276.6). Floats are now kept as float64; totals match the source exactly.

Evidence (QA, SQLite, this sandbox):
| Test | Result |
|---|---|
| 6,000-row CSV upload | encrypted; plaintext table 0 rows; ciphertext contains no "Lahore"/"North" |
| 650,000-row CSV (54.1 MB, Pro) | background job, progress 0→25→48→70→93→100, 3.8 s total |
| Ask AI battery on encrypted 6,000 rows | 17/17 correct, 10/10 refused, 2/2 missing flagged |
| Ask AI battery on encrypted 650,000 rows | 17/17 correct; first question 3.1 s (decrypt + load), then ~0.4–1.2 s |
| Decrypt 650k rows | 0.46 s; totals and counts equal pandas on the source file |
| Dataset page, details, health, exec summary, report builder, CSV/Excel export | 200, real values shown |
| Migration of 5 existing plaintext QA tables | migrated 5, re-run skipped all, sums unchanged |
| `tests/test_dataset_vault.py` | 7 passed (no plaintext, query correctness, float precision, missing key refuses, wrong key fails, migration, delete) |

Free gate: 1.09 MB CSV refused for Free (400, `upgrade_required`). Uploads above `UPLOAD_ASYNC_THRESHOLD_MB` (8 MB) run as background jobs with real progress.

Copy: unverified "10M+ rows/records" claims replaced with "up to 500 MB"; encryption copy now describes AES-256-GCM per-dataset keys.

## R-Phase 4 — Responsive and theme matrix (re-run)

456 screenshots: 38 pages (public, portal, admin) × 360/390/768/1024/1440/1920 × light/dark, logged in as a QA user and the Super Admin. Result: 0 pages with horizontal overflow; the only console errors are on the deliberate `/nope-404` check (12). Fixed from review:
- Portal top bar on phones wrapped the notification bell under the menu button; now one row.
- Decorative amber/yellow (dataset names, icons, theme toggle) rendered in colour; now ink. Status colours (success/danger) kept.
- Dashboard privacy panel said "raw dataset records are never transmitted externally". Not true: when the AI model is needed it receives the question, column names and up to 3 sample rows. Copy corrected.
- CSS cache-buster bumped (`mono.css?v=20261119`).

## R-Phase 7 (part) — Security
- `/dataset/delete/<id>` deleted on a plain GET, so a link on another site could delete a dataset (SameSite=Lax still sends cookies on top-level GET). Now: POST with CSRF token deletes; GET only redirects to the dashboard with a message. Route name/URL unchanged. Verified: GET → not deleted; POST without token → not deleted; POST with token → dataset, table, ciphertext and key removed.
- Vault cleanup on delete used a second connection while the delete transaction held locks; ciphertext was left behind on SQLite. Fixed: user delete removes it after commit; admin user-delete removes it inside the same transaction.
- Production must set `FORCE_SECURE_COOKIES=true` (Secure cookie + HSTS header are already wired to it).

## R-Phase 8 — Deploy readiness and auth hardening

### Configuration
- `PUBLIC_BASE_URL` helper (`utils/public_url.py`): emailed links, Google redirect URI and canonical/og:url tags are built from this one setting instead of the request host.
- `SUPPORT_EMAIL`: one setting for every support address on the site; default `muhammadashan7456@gmail.com` (`site_config.py`).
- `UPLOAD_ASYNC_THRESHOLD_MB`: default 50 (`uploads/upload_jobs.py`). QA: 487 KB file uploaded synchronously; 66.6 MB / 800k rows ran as a background job in about 5 s, and totals matched the source.
- `DEPLOY_UPDATE.md` rewritten for the Oracle Cloud VM (Nginx, systemd, SQL Server); old cloud-host wording removed.
- `.env.example` lists every variable the code reads.
- SQLite QA schema: `Datasets.InsightsEnabled` / `LastInsightScanAt` added, so the insights scan no longer fails each tick.

### Security
- One-time codes out of the session cookie (5db4a1e): pending sign-in sessions and `EmailVerificationTokens` hold an HMAC-SHA256 digest keyed with `SECRET_KEY` and bound to purpose + user; constant-time compare.
- **Password reset codes (new):**
  - Checked against the account's email + code, never by code alone. The page asks for the email, prefilled after "Forgot password".
  - Stored as a keyed digest (`password-reset` purpose), with a constant-time compare.
  - Every wrong code adds 1 to `PasswordResetTokens.Attempts` in the database (SQL Server migration already existed; SQLite migration added). The 5th wrong code invalidates the code (`IsUsed = 1`).
  - A new request retires older live codes. The reset link no longer carries the code.
  - One generic error for unknown email, wrong code, and invalidated code.
- **Super Admin email OTP (new):**
  - The code digest, issue time, wrong-code counter and lockout live on the admin's `Users` row (`AdminOtpHash`, `AdminOtpIssuedAt`, `AdminOtpFailedAttempts`, `AdminOtpLockoutUntil`; SQL Server `ALTER` migrations in `init_db` + SQLite schema/migration). The cookie now holds no code, digest or counter.
  - After 5 wrong codes, the account is locked for 15 minutes. The lock blocks code entry and stops a new code from being sent, even with the correct password.
  - Replaying an older cookie does not reset the count, and a new code does not reset it either.
  - A successful login or an expired lock clears the counter. The code is single-use and expires after 10 minutes.
  - Message: "For your security, verification is paused after 5 incorrect codes. Please wait about N minutes and sign in again."
- Tests: `tests/test_reset_and_admin_otp_limits.py` covers both fixes with 13 tests on a real temporary SQLite schema and a mocked clock. The old cookie-based admin OTP test in `test_auth_email_flows.py` was replaced by these tests.
- Live QA (local server, SQLite, SMTP sink), password reset:
  - Stored token is a 64-char digest.
  - Right code with another account's email or an unknown email: refused.
  - 5 wrong codes: DB `Attempts=5, IsUsed=1`, and the correct code is then refused.
  - All failure messages are identical.
  - A fresh code resets the password (302 to login); reusing that code is refused.
- Live QA, Super Admin OTP:
  - The cookie holds only user_id/name/email/role/next.
  - 5th wrong code locks the account (DB `AdminOtpFailedAttempts=5`, lockout set, code cleared).
  - The earlier cookie with the correct code: refused, and the dashboard redirects.
  - Correct password during the lock: no code sent.
  - After the lock time passed (simulated by editing the DB timestamp, not a real 15-minute wait), a new sign-in worked. The counter returned to 0 on success.

### Ask AI guardrails
27/27 in-scope questions correct, 20/20 off-topic questions refused, 2/2 missing columns flagged. Coding/how-to-fix questions are refused, and distinct counts are answered.

### Google sign-in
13 tests in `tests/test_google_oauth.py`.
- Google Cloud Console redirect URI: `https://aidataanalystpro.co.uk/auth/google/callback`
- JavaScript origin: `https://aidataanalystpro.co.uk`
- Env: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `PUBLIC_BASE_URL`

### Final checks (this sandbox)
- `pytest` (excluding Selenium files `test_auth.py`, `test_demo_sandbox.py`, `test_public_pages.py`): 125 passed, 0 failed.
- `python -m compileall -q .` (venv excluded): exit 0.
- `node --check` on all 6 `static/**/*.js`: 0 failures.

### NOT verified
- Real Google sign-in end to end with a real Google account in the new flow.
- Real SQL Server (the new `ALTER TABLE` migrations ran only as code review; QA ran on SQLite).
- Real email delivery (QA used a local SMTP sink).
- Live domain `aidataanalystpro.co.uk` with this branch deployed.
- Theme/mobile screenshot matrix for R-Phase 8 changes (Playwright system libraries missing in this sandbox).
- Scheduler log line `Auto-suspend error: near ".": syntax error` on SQLite: seen again in the QA server log, not investigated.

---

# R-Phase 9 — Market-ready pass (started 2026-11-20)

Order changed by the owner: functional work first (audit → auth → upload/encryption → AI → admin/billing/email → security → docs), design and performance last. QA env: Python 3.12 venv, SQLite QA DB (`ALLOW_SQLITE_FALLBACK=true`, throwaway key), local SMTP sink, one Waitress process on 127.0.0.1:5051. Playwright Chromium now runs in this sandbox (system libraries installed with apt); Lighthouse 12 via npm in `/workspace/work/tools` (outside the repo).

## R-Phase 9.1 — Audit and baseline

- Routes: 124 URL rules (30 admin, 26 auth, 17 api, 9 dataset, rest public/portal). List kept outside the repo in the QA folder.
- Tests: 125 passed (non-Selenium; `test_auth.py`, `test_demo_sandbox.py`, `test_public_pages.py` excluded).
- Internal link crawl (requests, one URL per pattern): anonymous 24 pages, logged-in user 67, Super Admin 36 — no 404/5xx (JS template-literal hrefs excluded).
- Domain: every default/doc/test uses `https://aidataanalystpro.co.uk`; no `dataanalystpro.co.uk` string in the repo.
- Lighthouse BEFORE (mobile emulation, local server, no Nginx gzip): `/` perf 99, a11y 98, BP 100, SEO 100, FCP 1.6 s, LCP 1.8 s, TBT 0 ms, CLS 0.007, 155 KB. `/auth/login` perf 99, a11y 98, BP 100, SEO 66 (deliberate `noindex`), FCP 1.5 s, LCP 2.0 s, 153 KB.

Failing / suspicious (to fix in later phases):
1. Scheduler `Auto-suspend error: near "."` on SQLite: `auto_suspend_overdue_users()` uses SQL Server-only `UPDATE … OUTPUT INSERTED.*`. It also sets `suspended`, while the plan rule is "Pro lasts 30 days, then reverts to Free".
2. Lapsed Pro users (status `expired`/`suspended`, still `IsActive=1`) see a red "ACCOUNT SUSPENDED" banner although they keep full Free access.
3. Ask AI refusal text is not the exact sentence required.
4. Password reset has no resend limit (each request mails a new code).
5. No per-IP rate limit on login / OTP / reset endpoints (only per-account lockout).
6. Still to check: Google button on register page, profit/loss prediction, chart-type coverage, invoice PDF, admin user list, startup PUBLIC_BASE_URL warning.

## R-Phase 9.2 — Authentication and Google account linking

Already in place and re-verified by tests: Google on login AND register (`btn-google-signup` on `auth/signup.html`, which `/auth/register` renders), PKCE + state, `email_verified` required, new Google users verified + `free` with no OTP/payment step, linking to an existing account by verified email, hashed OTPs, reset-code 5-try limit, admin OTP 15-minute lockout in the DB.

Fixed now:
- `database/queries.py` `get_user_by_email` / `get_user_by_username`: compare with `LOWER(..) = LOWER(?)`. Root cause: login and Google linking lowercased the input, so on a case-sensitive database a migrated user stored as `Name@Example.COM` was not found (Google would even have created a second account). SQL Server's default collation already ignores case, so behaviour there is unchanged; on SQLite it is now the same.
- `auth/routes.py` forgot-password: server-side resend limit — one code per 60 seconds and at most 3 per hour per account (`count_recent_reset_requests()`, counted from `PasswordResetTokens.CreatedAt`). Before, every request minted a new code with a fresh 5-try budget, which sidestepped the per-code limit. A DB error fails closed. The page message is the same for throttled, unknown and normal requests (no enumeration) and now says honestly that codes are limited.
- `auth/routes.py` login: the failed-login message (same for unknown account and wrong password) now tells Google-created users to use Continue with Google or set a password with Forgot password.

Tests added (16 + 16 in the two files, all pass):
- `tests/test_google_oauth.py`: legacy mixed-case email links to the same account (no new row, password and plan kept); form user → Google → form again lands on the same `UserID`; Google-only user → Forgot password → form login and Google both open the same account.
- `tests/test_reset_and_admin_otp_limits.py`: 60-second spacing, 3-per-hour cap then allowed after an hour, throttled message identical to unknown-email message.
- Full non-Selenium suite: 131 passed.

Not verifiable here: a real Google round-trip (no client secret in this sandbox; Google is mocked) and SQL Server execution of the new query text.

## R-Phase 9.3 — Upload gate, plans and encryption

Re-verified live (QA server, SQLite, one process):
- Free user, 487,559-byte CSV: HTTP 200, stored in 1.16 s. Free user, 1,142,858-byte CSV: HTTP 400 `upgrade_required=true` (server-side). >500 MB rejection stays covered by `tests/test_upload_limits.py`.
- Pro user, 66,610,623-byte CSV (800,000 rows): POST returned a background job in 0.44 s; progress 0→20→39→57→75→99→100; done 4.46 s after the request started.
- Stored form: plaintext table 0 rows for both uploads; vault meta 800,000 rows / 17 chunks, 6,000 rows / 1 chunk; first chunk contains no "Lahore"/"Laptop" bytes.
- Preview pagination on the 800k-row dataset: page 1 (100 rows) 0.16–0.20 s / 43 KB; page 4,000 0.17 s; the largest page size (5,000 rows) 0.44 s / 1.4 MB. Never renders all rows.

Fixed:
- **Pro lasts 30 days, then Free** (`utils/scheduler.py`, `database/queries.py`): the sweep used SQL Server-only `UPDATE … OUTPUT INSERTED.*` (root cause of the SQLite log line `Auto-suspend error: near "."`) and set users to `suspended`. Now a portable SELECT + guarded per-user `UPDATE` (`get_lapsed_pro_users()`, `revert_user_to_free()`) sets `SubscriptionStatus='free'`, sends one "Pro plan ended" email and an in-app notice, and is idempotent. Rows the old sweep marked `suspended` for expiry (still `IsActive=1`, reason = the auto-expiry text) return to Free quietly (no repeat email). Admin bans (`IsActive=0`) and admins are never touched. `SQLiteCursorAdapter.rowcount` added (pyodbc-compatible).
- `utils/subscription.py` + `templates/base.html`: a lapsed plan no longer shows a red "ACCOUNT SUSPENDED" banner; it shows "Your 30-day Pro plan ended on …, you are on the Free plan now". "Suspended" is shown only for a real admin ban.
- `auth/email_service.py` `send_subscription_suspended_email` (name kept for callers) now says the plan ended and the account is on Free, with a renew link from `PUBLIC_BASE_URL`.
- New admin page `/admin/datasets` (`admin.manage_datasets`, `templates/admin/datasets.html`, sidebar link): dataset **metadata only** (file, owner, rows, columns, size, upload date, encrypted / not migrated), searchable, paginated in the database.
- Honest privacy copy (`privacy.html`, `faq_page.html`, `security_page.html`): the server decrypts in memory to answer; anyone controlling both the server and the master key could read data; master key kept outside the DB and backed up separately. Removed inaccurate "Argon2id/PBKDF2" (it is bcrypt), "TLS 1.3" and "bank-grade".

Tests added: `tests/test_admin_never_sees_rows.py` (3: a marker row stored through the real vault never appears on 10 admin pages or 4 admin exports, including inside xlsx; the admin code never calls a row-reading function), `tests/test_plan_expiry.py` (3: who returns to Free, emails, idempotent, banner). Full non-Selenium suite: 137 passed.

Schema: none (the sweep only changes values in existing columns).
