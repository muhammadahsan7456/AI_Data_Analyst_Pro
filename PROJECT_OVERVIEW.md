# AI Data Analyst Pro — Project Overview

*This document describes the project exactly as its code currently behaves. It was produced by reading the actual source (routes, templates, and core logic) rather than assuming from file/folder names. Where something couldn't be confirmed with full certainty from the code alone, that is said explicitly rather than stated as fact. This is a living document — it is meant to be kept in sync and updated whenever new features, fixes, or structural changes land, not just written once.*

---

## The Three Panels, At a Glance

This project has three distinct, separately-accessed areas. Everything else in this document is detail under one of these three.

| # | Panel | Who sees it | Entry point | Covered in |
|---|-------|-------------|--------------|------------|
| 1 | **Public Marketing Site** | Anyone, not logged in | `/` (home) | §3 (the public-facing parts: home, pricing, Reviews, FAQ, About, Docs, Help Center, Contact, Demo) |
| 2 | **User Portal / Dashboard** | Any registered, logged-in user (free or Pro) | `/dashboard` after login | §3 in full — upload, Ask AI chat, forecasting, exports, dataset profiling, profile/settings, payment receipts, scheduled reports, alert rules |
| 3 | **Super Admin Console** | Only accounts with `Role = SuperAdmin` or `Admin` (same access either way — see §4) | `/admin/login` (separate login + OTP, not the normal user login) | §5 in full — user management, payment approval, platform settings, Reviews management, audit log, analytics dashboard, contact-message inbox, newsletter subscriber list |

All three run from the same Flask codebase and the same database — there's no separate deployment per panel. A single logged-in user with the right role can move between panel 2 and panel 3 (there's a "Super Admin Console" link in the user sidebar for such accounts, and an "Analyst Portal / Dashboard" link back, the other way, inside the admin console).

---

## 0. Recent Work Log (this engagement)

Everything below was found, fixed, and **live-tested** (real browser/real HTTP requests, not just read-and-assumed) across several passes. Grouped by category, not chronological.

**Bug fixes — core app**
- Stuck/infinite loading bar after a file download finished (exports, chart downloads, cleaned-dataset CSV, report-builder HTML, admin CSV/Excel exports) — the loading indicator now correctly recognizes a download instead of waiting for a "page navigation" that was never going to happen.
- Voice input cutting off a user's question too early — silence-timeout increased and the mic UI made clearer about when it's safe to stop talking.
- Ask AI responses were slow on first use after an upload — the schema is now pre-warmed in the background right after upload instead of being computed cold on the first question.
- `NaN` / broken serial numbers appearing in result tables and a "Total Records" KPI card.
- "Smart Dataset Suggestions" were hardcoded/generic — now genuinely generated from each dataset's real schema and sampled values.
- Enterprise Settings page: the "Privacy Level" and "AI Model" dropdowns were saved but had **no effect** — both are now fully wired into the Ask AI pipeline (confirmed: `privacy_level` controls what's sent to the LLM, `ai_model` controls which OpenRouter model is tried first).
- Security page: raw unformatted timestamps, a hardcoded static IP, non-unique Log IDs — all fixed to show real, correctly formatted, unique data. The same raw-timestamp bug was also found and fixed on the Profile page's "Joined" date.
- **Critical billing bug**: every newly registered free-tier user was silently getting 30 days of full Pro access instead of the intended free tier — root-caused to a wrong default value and an unconditional startup `UPDATE`, both fixed.
- Documentation/FAQ/About Us/Help Center pages were ejecting logged-in users out of their dashboard context (showing the public marketing shell instead); a follow-up pass then found the FAQ/Help/About/Docs pages were visually broken for logged-in users specifically, because the dashboard shell never loaded the stylesheet those pages' content depends on — fixed while **keeping** the correct sidebar/context behavior.
- System Health page's CPU/RAM and the admin "Infrastructure Health" panel were static placeholder numbers — both now read real, live metrics (`psutil` for CPU/RAM, an actual timed `SELECT 1` for DB latency, real cache hit-rate, real request/error counters, real uptime).
- Admin sidebar incorrectly showed "Sign In / Create Account" instead of "Sign Out" on `/auth/*` account pages (profile, security, etc.) for a genuinely logged-in user — a context-loading shortcut was skipping those pages by mistake.
- Super Admin "Payments & Billing" screenshots: **every** payment showed "Screenshot unavailable" regardless of whether the file actually existed, because of one broken import that was silently swallowed by an exception handler. Fixed — screenshots that exist now display correctly; only genuinely missing files still show as unavailable.
- "Backup Now" on Platform Settings genuinely takes ~15-20 seconds with zero visual feedback, which looked like a frozen page — the button now shows a clear "Backing up… please wait" state immediately on click.
- `.xls` (legacy Excel) file uploads were silently broken (missing `xlrd` dependency) despite being advertised as a supported format; `pyarrow` (Parquet/Feather) and `lxml` (XML) were working only by chance (installed but never declared in `requirements.txt`) — all three are now proper, declared dependencies, and all 9 supported formats were re-verified end-to-end with real file uploads.
- The "9 Enterprise File Formats" icons on the public Features page had near-duplicate icon/color pairs (Excel .xlsx vs .xls, TSV vs .txt) that looked like repeated entries — each format now has a visually distinct icon/color.

**New features**
- **Dynamic Reviews/Testimonials**: previously fully hardcoded on the public `/reviews` page. Now backed by a real `Reviews` database table, with a full Super Admin management section (add/edit/hide/delete, matching the existing admin console's design) at `/admin/reviews`, and the public page renders live from the database.
- **Optional "Company Name" field**: added to registration (genuinely optional — signup succeeds with it blank) and to the Profile page (reuses an already-existing `UserProfiles.Company` column, no schema change needed for registration).
- **Company Logo**: repurposes the existing profile-photo upload field rather than adding a second upload control — relabeled "Profile Photo / Company Logo," same validation/resize logic, same no-image default behavior.
- **Company name display**: shown under the user's name in the dashboard sidebar ("Acme Corp" style subtitle), falling back to the original "Active Account" text with no layout change when no company is set.

**Infrastructure / email**
- Switched outbound email from Gmail SMTP (which hit Gmail's daily sending limit) to **Brevo SMTP** (free tier, 300 emails/day).
- Fixed a real bug this switch exposed: the email "From" address was built from the SMTP login rather than the verified sender address — harmless on Gmail (where they happened to be identical) but caused every email to be silently rejected by Brevo (mismatched/unverified sender). Now resolves correctly from the verified sender email.
- Every outbound email (all ~15 email types — OTP, verification, password reset, payment status, subscription reminders, newsletter, contact replies, etc.) now carries the project's logo, embedded directly in the email (not a hotlinked image, so it displays immediately in any email client without depending on this app being publicly hosted).

---

## 1. Project Summary

AI Data Analyst Pro is a Flask-based web application that lets a business user upload a dataset (CSV, Excel, JSON, XML, TSV, Parquet, or Feather), and then interact with it without writing any SQL or code: ask plain-language questions and get back an answer, a chart, and an auto-generated explanation; view and search the raw data; run a simple trend forecast; export the data or a full report as Excel/PDF/Word/PowerPoint; and share scheduled report emails or threshold-based alerts with others.

It is built as a small SaaS: anyone can register for free and use the platform on datasets under 1 MB; uploading anything 1 MB or larger requires a paid "Pro" plan, unlocked through a manual payment-proof-and-approval flow (no payment gateway is integrated — a human Super Admin reviews a screenshot and approves it). A separate Super Admin console manages users, payments, platform-wide settings, and support messages.

---

## 2. Tech Stack

- **Backend**: Python / Flask (`Flask-WTF` for CSRF + forms), served by **Waitress** in production (`wsgi.py`) and Waitress-or-Flask-dev-server locally (`run.py`).
- **Database**: Microsoft SQL Server via `pyodbc`, using raw parameterized SQL (no ORM) — `database/connection.py` (connection/pooling/schema init) and `database/queries.py` (centralized query builders). A local SQLite fallback exists for development only, and is off by default (`ALLOW_SQLITE_FALLBACK`).
- **Frontend**: server-rendered Jinja2 templates (`templates/`), vanilla JavaScript (no SPA framework), Plotly.js for interactive charts, Chart.js for the admin dashboard's trend charts, Bootstrap classes on some report-style pages.
- **AI / LLM**: despite the module being named `ai/gemini.py`, it does **not** call Google Gemini — it calls **OpenRouter** (an LLM aggregator) via the OpenAI Python SDK. Models tried, in order, with a short timeout: `openai/gpt-4o-mini`, `meta-llama/llama-3.3-70b-instruct`, `qwen/qwen-2.5-coder-32b-instruct`. Most of the "AI" question-answering is actually custom deterministic Python logic, not the LLM — see §3.4.
- **Email**: SMTP (Brevo, configured in `.env` — switched from Gmail SMTP after hitting Gmail's daily sending cap) via a custom `EmailService` class, used for OTP codes, payment notifications, scheduled reports, and alerts. Every email embeds the project logo as an inline (CID) attachment, not a hotlinked image.
- **File parsing / data pipeline**: pandas, openpyxl (Excel), custom multi-format loader (`uploads/multi_loader.py`).
- **Charting**: Plotly specs built server-side for the interactive web view; Matplotlib for static chart images used in exported documents and the "Download HD Chart" button.
- **Document export**: ReportLab (PDF), python-docx (Word), python-pptx (PowerPoint), openpyxl (Excel).
- **Security**: bcrypt (password hashing), `cryptography.fernet` (field-level encryption of uploaded dataset text columns).
- **Forecasting**: a simple linear-trend model (NumPy `polyfit`) with residual-based confidence intervals — not a heavyweight ML library, despite scikit-learn being a listed dependency (scikit-learn's actual usage wasn't traced as part of this document).

**Folder layout** (high level, matching the project's own README):
- `run.py` / `wsgi.py` — Flask app entrypoints (dev vs. production)
- `database/` — connection handling + SQL query builders
- `ai/` — the Ask-AI pipeline, forecasting, data-quality/insights, privacy sanitization
- `auth/` — signup, login, OTP, password reset, sessions, security helpers
- `admin/` — the admin/Super Admin console
- `frontend/` — the main app routes (dashboard, upload, chat, dataset views, exports, pricing, etc.)
- `templates/` — all Jinja2 HTML
- `static/` — CSS/JS/images
- `uploads/` — file ingestion pipeline (parsing, async job processing for large files)
- `visualization/` — chart generation (interactive + static)
- `utils/` — caching, encryption, scheduler, exporters, subscription logic, alerting

---

## 3. User-Facing Features (Frontend)

### 3.1 Registration, login, and session handling
Registration is free and immediate: a user fills in name/username/email/phone/country/city/password, agrees to terms, and submits — no plan selection or payment step appears anywhere in this flow. On success the account is created with `SubscriptionStatus = 'free'` and a 6-digit OTP is emailed; the user must enter it (10-minute expiry, 5 wrong attempts allowed, max 3 resends per 15 minutes) before they can log in. Login uses email-or-username + password; **5 wrong passwords locks the account for 15 minutes** (`FailedLoginAttempts`/`LockoutUntil` columns), reset on a successful login. Passwords are hashed with bcrypt and must be at least 8 characters with upper/lower/digit/special character. Sessions are HttpOnly, SameSite=Lax cookies with a 1-hour lifetime, and the app separately enforces a 1-hour inactivity timeout on every request. "Forgot password" uses the same OTP-email mechanism.

### 3.2 Dashboard
After login, the dashboard shows: total datasets, total rows uploaded, total AI queries run, total charts generated, total storage used, the most recently uploaded dataset, the largest dataset by row count, the largest by storage size, the 4 most-asked questions, the 4 most-recently-opened datasets, and a paginated (10 per page), searchable/sortable/date-filterable list of all the user's datasets. A banner appears here if a payment is pending review or a Pro plan is expiring/expired. Dashboard metrics are cached server-side for 60 seconds per user (so a repeat visit within a minute is fast); a search clears the cache for that request.

### 3.3 Dataset upload — exact size-gating logic
Supported formats: CSV, Excel (.xlsx/.xls), JSON, XML, TXT, TSV, Parquet, Feather. The size rule, enforced **server-side** (confirmed by direct-request testing, not just the browser's JavaScript check):
- **Under 1,048,576 bytes (1 MB)**: uploads immediately, free, no restriction, for any logged-in user.
- **1,048,576 bytes or larger, up to 500 MB**: blocked unless the user's account is currently "Pro" (`is_pro` — see §3.6). A free user sees an upgrade prompt instead of the file being processed; if they've already submitted a payment proof that's awaiting approval, they see a distinct "pending approval" message instead of being asked to pay again.
- **Over 500 MB**: rejected outright for everyone, with a clear "maximum file size is 500 MB" message — no plan covers this.
- Files at/above 8 MB (configurable) are handed to a background thread with a job ID the page polls for progress, so a large upload doesn't hold the browser on a stalled request; smaller files are processed synchronously and the result appears immediately.
- On ingestion, each uploaded file becomes its own physical SQL table; text/object-dtype columns are passed through Fernet-based field-level encryption before storage (with a few column names like `RecordID`/`ID` excluded).

### 3.4 Ask AI (natural-language chat)
A user types a question in plain English (or informally, including Roman Urdu city terms per code comments) about a selected dataset. What actually happens, step by step:
1. If no dataset is explicitly chosen, the system scores the user's datasets by word-overlap with the question against each dataset's filename/tags/columns and auto-picks the best match.
2. A per-user daily quota (default 150 questions, exempting admin roles) is checked.
3. The question is handed to a pipeline with a **25-second hard timeout** — past that, the user gets a "try a more specific question" message rather than an indefinite spinner.
4. **Most of the "AI" is not the LLM** — it's a layered deterministic system: a fast pattern/rule-based SQL generator and a structured "query plan" engine (built from regex-detected intent, filters, aggregations, group-bys) try first; dedicated modules resolve which column a vague term like "revenue" or "top city" actually refers to, detect and apply filters (city names and aliases, date ranges, numeric thresholds, status values) by sampling the real distinct values in that column. The LLM is only called as a fallback when these deterministic paths can't produce usable SQL, and again if the generated SQL errors out against the database (an automatic "fix this SQL" retry, up to 3 times, with a confidence score that decreases per retry).
5. Only `SELECT` statements are allowed; destructive SQL keywords are explicitly blocked both when cleaning LLM output and again right before execution. All literal values are parameterized, not string-concatenated.
6. If a question names a value that genuinely isn't in the dataset, the system is designed to say so explicitly rather than silently running an unfiltered query and returning a plausible-looking but wrong number.
7. Once a result comes back, a business-language explanation is generated (LLM-based, with a deterministic statistical fallback if the LLM is slow/unavailable), a chart type is chosen automatically from the result's shape (deterministic, not LLM) and built as an interactive Plotly chart, and everything is returned together.
8. **Follow-up questions**: only partially supported. If a new question contains words like "sort"/"descending" and there was a previous question this session, the system re-runs a text-spliced version of the old question with the sort instruction appended. General conversational follow-ups ("now break it down by city") are **not** specially handled — each other question is treated independently. (Some conversational-context code exists in the codebase but is not actually wired up — see §8.)

### 3.5 Data views, search, filter, export
- **Dataset table preview**: paginated view of the raw uploaded data (page sizes 10/25/50/100/500/1000, or "all" capped at 5,000 rows per page), true database-level pagination.
- **Dataset profiling ("Details")**: a data-quality report, AI-generated insights, and (when there are 2+ numeric columns) a correlation heatmap, computed from up to the first 5,000 rows.
- **Smart search**: a live-search API across a user's own datasets by filename/name/tags.
- **Multi-dataset analysis**: pick 2+ of your datasets and ask a question that compares/joins/merges/unions them, or finds duplicates across them — same underlying AI pipeline philosophy as single-dataset chat.
- **Exports**: a dataset (or a chat query's result) can be exported as CSV, Excel, a PDF report (with auto-generated insights and a chart), a Word report, a PowerPoint deck, or a standalone SVG chart. An "Executive Report Builder" also generates a full branded HTML executive briefing per dataset.

### 3.6 Pricing / subscription / payment flow
- **Current price**: PKR 1,499/month for the "Pro" plan. The free tier has no time limit and no payment step ever.
- **What triggers a payment prompt**: only the 1 MB+ upload gate described in §3.3 (plus an optional "Upgrade" link visible elsewhere, e.g. the dashboard/upload page banners).
- **Payment methods**: Easypaisa, NayaPay, and SadaPay (all shown consistently wherever a payment method can be chosen), all tied to the same published mobile-wallet number; there is no integrated payment gateway (no Stripe/card processing actually wired in, despite a "Stripe" filter option existing in the admin payments list).
- **Submitting proof**: the user enters their registered email, selects a method, optionally a transaction ID, and uploads a screenshot (image, max 5 MB); a duplicate submission is blocked while a prior one is still pending.
- **Granting access**: entirely manual — a Super Admin reviews the screenshot/details and approves or rejects it (see §5). On approval, the account becomes "Pro" for **exactly 30 days from the approval moment** (or extended by 30 days from the existing end date, if renewing before expiry); on expiry, the account automatically reverts to the free tier's 1 MB limit — no manual admin action needed to downgrade, and nothing else about the account (dashboard, Ask AI, small uploads) is restricted while free or expired.
- A user can see their current plan status, upload limit, and expiry date on their Profile page and (for Pro users) on the upload page banner.

### 3.7 Profile / account settings
On the Profile page a user can update their name, username, email, phone, country, city, a profile photo / company logo (auto-resized; the same upload field doubles as the company logo — see §0), bio, occupation, **company name** (also settable, optionally, at registration), department, designation, website/LinkedIn/GitHub/portfolio links, timezone, and display language, plus see a "Plan & Billing" summary (current plan, upload limit, expiry, and a direct upgrade/renew link if applicable). A separate "Enterprise Settings" page lets a user set a "privacy level" and a preferred AI model from a dropdown — both are genuinely wired into the Ask AI pipeline: privacy level controls how much raw data is included in prompts sent to the LLM, and the AI model choice controls which OpenRouter model is tried first. A "Security & Sessions" page and password-change flow also exist under auth settings.

### 3.8 Other user-facing features
- **Forecasting**: pick a dataset and a number of days, get a simple linear-trend forecast with a confidence band and chart; the date and target columns are auto-detected by keyword/dtype heuristics.
- **Scheduled reports**: a user can schedule a recurring (daily/etc.) executive-summary email for a dataset to their own verified email, at a chosen time — this is genuinely processed by a background scheduler, not just saved and forgotten.
- **Alert rules**: a user can set a metric/operator/threshold (e.g. "Return Rate > 20") on a dataset and get emailed when it's breached — also actually evaluated by the same background scheduler.
- **Payment receipts**: a user can view and download a PDF invoice/receipt for any of their past payments.
- **Voice input**: the chat page includes a microphone/voice-to-text affordance (browser speech recognition feeding the normal question box) with a tuned auto-submit silence timer; this appears to be handled client-side rather than through a distinct server-side voice pipeline.
- **In-app AI notifications**: a bell/notification feed of system events (uploads, exports, payment status changes, etc.), markable as read or clearable.
- **Newsletter subscribe/unsubscribe**, a public **contact form**, a dynamic **Reviews/Testimonials page** (database-backed, managed by Super Admin — see §5), and static informational pages (About, FAQ, Docs, Privacy, Terms, Security overview, Help Center, Demo).

---

## 4. Admin Panel Features

The codebase defines two role names, `Admin` and `SuperAdmin`, but **there is no actual privilege difference between them anywhere in the code** — every admin-console gate (`admin_required`, the admin login flow, every route in `admin/routes.py`) checks for `Role in ["SuperAdmin", "Admin"]` as one combined group, with no route restricting any feature to only one of the two. Oddly, the "Change Role" dropdown in the console itself doesn't even offer "Admin" as an assignable option (only User / Analyst / SuperAdmin) — so in practice, an "Admin" role only ever exists if set directly in the database, and if it does, that account gets the full Super Admin console with no lesser tier. Because of this, there is nothing meaningfully separate to describe for a "regular Admin" role — everything below in §5 applies equally to both.

---

## 5. Super Admin Panel Features

Reached via a separate login at `/admin/login` (distinct from the normal user login, same 5-attempt/15-minute lockout policy), which additionally requires a **6-digit email OTP** (10-minute expiry, 5 attempts) as a second factor before the admin session is established. The admin session uses its own session keys and is force-expired after **1 hour of inactivity**.

- **User management**: a searchable/filterable list of every user (name, email/phone, location, dataset count/storage/row usage, IP address, role — editable inline, plan-status badge, plan expiration date, registration date), exportable as CSV/Excel. Per-user actions: change role, manually activate/extend a subscription (grants a fresh 30-day period from now — note the button is labeled "+30 Days" but the underlying action is a fresh-month activation, not literally adding 30 days to the existing date), suspend/un-suspend (toggles account access; suspending also records an optional reason), and delete (cascades: drops the user's physical dataset tables, deletes their query history/AI conversations/audit logs/payments/datasets, then the user row — irreversible, confirmed only by a JS prompt, no soft-delete). There's no separate "user detail" page beyond the list row itself.
- **Payment approval workflow**: pending payments show transaction ID, user, plan, amount, method (icon-coded per method), a zoomable screenshot, date, and status; filterable by status/method/date/search, exportable, with a printable/PDF invoice view. **Approving** a payment under PKR 1,499 is blocked outright; otherwise it either extends the user's existing end date by 30 days (if they're renewing before their current Pro period lapses) or grants a fresh 30-day period (new signup, previously expired/suspended/rejected) — either way the user is emailed and gets an in-app notification. **Rejecting** requires a typed reason, marks the account `rejected` (which, per §3.6, still leaves full free-tier access intact), and emails/notifies the user with that reason.
- **Site-wide settings**: editable payment account details (plan name/price label, bank/JazzCash/Easypaisa/SadaPay numbers, instructions text) that feed directly into the user-facing payment screens; a site-wide announcement banner (title/message/on-off) shown on user dashboards; a manual "Backup Now" button on top of the daily automatic backup (now shows a clear "Backing up…" state while it runs, since a real backup takes ~15-20 seconds). An "Infrastructure Health" panel on this page shows real, live telemetry (DB round-trip latency, in-memory cache size/hit-rate, active background upload jobs, process uptime, total requests/errors).
- **Reviews / Testimonials management**: add, edit, hide/show, or delete the reviews shown on the public `/reviews` page — fully database-backed, changes reflect on the public page immediately.
- **Audit log viewer**: the most recent 150 events (user, action, description, IP, timestamp) covering logins/lockouts, role/status changes, deletions, payment decisions, settings/announcement changes, exports, backups, and support-message actions — this view has no search/filter controls of its own.
- **Platform analytics dashboard**: total/active/suspended users, total revenue, completed/pending payment counts, total datasets, counts of users expiring soon/already expired, a shortlist of users expiring soon, the 5 most recent users and payments, the 8 most recent audit log entries, and two trend charts (revenue, signups) over a configurable date range (default last 6 months).
- **Contact/support message handling**: a full inbox for the public contact form — searchable/status-filterable, mark as New/Read/Responded, reply (which emails the customer, quoting their original message), and delete.
- **Other**: newsletter subscriber list management (view/search/delete), bulk CSV/Excel export for both the Users and Payments lists.

---

## 6. Security & Infrastructure

- **CSRF protection**: enabled application-wide (Flask-WTF `CSRFProtect`); a dedicated error handler gives a friendly "session expired" message and redirect instead of a raw 400 on a stale/invalid token.
- **Security headers** are set on every response: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`, a restrictive `Permissions-Policy`, and a Content-Security-Policy (`default-src 'self'`, with explicit allowances for the CDN/font/ad resources the app actually uses; `'unsafe-inline'` is kept for scripts/styles as a known tradeoff rather than using nonces). HSTS is added only when the app is configured to run behind HTTPS.
- **Login protection**: 5 failed password attempts locks an account for 15 minutes (same policy for normal and admin logins); OTP codes cap at 5 wrong attempts and 3 resends per 15 minutes. There is **no general IP-based rate limiting** anywhere in the codebase beyond these specific counters.
- **Password storage**: bcrypt with a strength policy (8+ chars, upper/lower/digit/special character required).
- **Role-based access control**: a logged-in user is blocked from the app only if their account was explicitly deactivated by a Super Admin — every subscription state (free, pending, active, expired, suspended, rejected) still gets full access to the app itself, with the file-size upload gate as the only separate restriction. Admin routes additionally require the SuperAdmin/Admin role.
- **Field-level encryption**: uploaded dataset text columns are encrypted at rest with Fernet (keyed by an env-configured key), decrypted transparently on read; a few ID-like column names are excluded. If no encryption key is configured, the app auto-generates one and writes it into the local `.env` file — worth being aware of operationally.
- **SQL injection prevention**: all queries are parameterized; dynamically-referenced table/column names (since each upload becomes its own table) go through a strict allowlist check before being used in SQL.
- **File upload validation**: restricted by file extension only (csv/xlsx/xls/json/xml/txt/tsv/parquet/feather); no content/MIME-type sniffing was found. A hard 508 MB request-body ceiling is enforced at the web-server layer with a friendly error page, on top of the app's own 500 MB check.
- **Background processing**: files at/above roughly 8 MB are ingested on a background thread with a pollable job ID, rather than holding the HTTP request open.
- **Caching**: an in-memory, thread-safe, TTL-based cache is used for expensive lookups (e.g. database schema/column lists) and for dashboard metrics (60-second cache per user).
- **Database connections**: connection pooling is explicitly enabled for SQL Server.
- **Compression**: HTML/CSS/JS/JSON responses are gzip-compressed server-side when the client supports it.

---

## 7. SEO & Performance Measures in Place

- **SEO**: `/sitemap.xml` and `/robots.txt` are generated dynamically (not static files) — the sitemap lists the public marketing/info pages, and robots.txt disallows all logged-in app areas (dashboard, upload, admin, auth, API routes). The ~17 public-facing pages (home, pricing, FAQ, about, docs, etc.) all carry full meta description/keywords, canonical URL, Open Graph, and Twitter Card tags, plus schema.org JSON-LD structured data (a handful of pages override the default with more specific structured data). The logged-in application pages (dashboard, chat, upload, admin console, etc.) intentionally carry none of this, consistent with being non-public, robots-disallowed screens.
- **Performance**: static assets are cached by the browser for 24 hours; dynamic/authenticated pages are explicitly marked not to be cached. Dataset table previews use true database-level pagination (SQL `LIMIT/OFFSET`). The dashboard's own dataset list and both of the admin console's Users and Payments lists, however, load the **entire** matching result set into the app before paging/filtering in Python rather than at the database level — functional today, but something to watch as those tables grow (see §8).

---

## 8. Known Limitations / Observations

*Factual observations only — nothing here has been changed.*

- Conversational follow-up handling is narrow: only a "sort/order" keyword case is specially handled by re-splicing the previous question's text; a richer conversational-context mechanism exists in the code (`set_conversational_context`/`get_conversational_context` in the query-plan engine) but is defined and never called anywhere.
- A `session["last_city"]` reference is read in the chat route but no code path appears to set it — looks like a partially-removed or half-finished feature.
- `ai/context_engine.py` and `ai/voice.py`'s `clean_voice_command()` function do not appear to be called from anywhere in the codebase.
- The "Admin" role exists as a permission check everywhere but has no way to actually be assigned through the admin console's own UI, and carries identical access to "SuperAdmin" wherever it is checked — there is effectively no two-tier admin system today despite the two role names.
- The admin console's "+30 Days" extend-subscription button actually grants a fresh 30-day period from the moment it's clicked, not literally +30 days added to the user's current end date.
- The admin Users list and Payments list both load every matching row into memory with no database-level pagination; this will get slower as those tables grow.
- No payment gateway is actually integrated (no Stripe/card processing, despite a "Stripe" filter option existing in the admin payments list) — all payments are manual proof-and-approval.
- No dataset-upload content/MIME-type validation was found beyond checking the file extension.
- No general-purpose IP-based rate limiting exists anywhere in the app outside of the specific login/OTP attempt counters.
- If the dataset-encryption key isn't pre-configured, the app silently generates one and writes it into the local `.env` file on first run.
- A code comment in `database/connection.py` notes that table-level encryption is intentionally not applied in one code path "to ensure 100% C-speed SQL filtering" — worth confirming this aligns with the intended data-protection scope.

---

## 9. Possible Future Enhancements

*Suggestions only — nothing here has been implemented.*

**User Experience**
- Richer conversational memory for Ask AI (reuse the already-defined but unwired `conversational_context` functions) so follow-ups like "now by city" work without re-typing the whole question. *Medium effort — the groundwork already exists in the code.*
- A proper in-app billing/subscription history view beyond the current receipts list (e.g. a timeline of past Pro periods). *Small effort.*
- Saved/favorite questions or a query template library for repeat analysts. *Small–medium effort.*

**AI / Analytics Capabilities**
- Replace the linear-trend forecaster with a seasonality-aware model (e.g. basic exponential smoothing or Prophet-style decomposition) for datasets with weekly/monthly patterns. *Medium effort.*
- Expand the multi-dataset analysis feature with saved cross-dataset "joins" a user can re-run without re-selecting datasets each time. *Medium effort.*

**Admin Tooling**
- Decide whether "Admin" should be a genuine lesser-privileged role (e.g. no payment approval, no user deletion) or remove the unused distinction entirely, since it currently exists as dead permission surface. *Small–medium effort, mostly decision + guardrail code.*
- Add server-side pagination/filtering to the admin Users and Payments lists before they grow large enough to matter. *Medium effort.*
- Add search/date filtering to the audit log viewer. *Small effort.*

**Security**
- Add general-purpose IP-based rate limiting (e.g. Flask-Limiter) beyond the existing per-account attempt counters, to reduce exposure to distributed brute-force or scraping. *Small–medium effort.*
- Add content/MIME-type validation (not just extension) on dataset uploads. *Small effort.*
- Move the dataset-encryption key fully out of auto-generation-into-`.env` and require it be explicitly provisioned in production. *Small effort.*

**Performance**
- Move the dashboard dataset list to true SQL-level pagination rather than fetch-all-then-slice. *Small–medium effort.*

**Monetization / Business**
- Integrate an actual payment gateway (Stripe, or a local Pakistani payment aggregator) to remove the manual screenshot-review bottleneck, while keeping the manual flow as a fallback. *Large effort.*
- Add an annual billing option at a discount to the current monthly PKR 1,499 price. *Small–medium effort (mostly pricing/plan logic, reuses the existing approval flow).*
- Usage-based tiers beyond the current single Free/Pro split (e.g. a mid-tier by row count or feature set) for larger customers. *Medium–large effort.*
