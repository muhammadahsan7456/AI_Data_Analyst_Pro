# Deploying an update — Oracle Cloud VM

Target: one Oracle Cloud Ubuntu VM running

- **Nginx** (TLS for `https://aidataanalystpro.co.uk`, proxies to `127.0.0.1:5000`)
- **one systemd service** running `python wsgi.py` (Waitress) from a Python venv
- **SQL Server on Linux** on the same VM (`127.0.0.1,1433`)

Run exactly ONE app service/process. The app owns a scheduler thread and in-process
upload jobs; a second process would double-run scheduled jobs and lose job progress.

Set these once in your shell before following the steps (adjust to your VM):

```bash
export APP_DIR=/opt/AI_Data_Analyst_Pro          # git checkout containing run.py and .env
export SERVICE=aidataanalystpro                  # systemd unit name (aidataanalystpro.service)
export DB_NAME=AI_Data_Analyst_Pro               # same as DB_NAME in .env
export SA_USER=sa                                # a SQL login allowed to BACKUP/RESTORE
export STAMP=$(date +%Y%m%d-%H%M%S)
export BK=/var/backups/aidap/$STAMP              # this deploy's backup folder
sudo mkdir -p $BK && sudo chown "$USER" $BK
```

## 0. What this release (branch `release/final-polish`, R-Phase 9) changes

**Schema** — added automatically by `init_db()` when the service starts. Every change is
additive and idempotent (`IF NOT EXISTS (SELECT * FROM sys.columns ...) ALTER TABLE ... ADD`),
all new columns are nullable or have a default, nothing is renamed, dropped or rewritten,
and the previous code simply ignores them:

| Table | New column | Type | Purpose |
|---|---|---|---|
| `Payments` | `InvoiceNumber` | `NVARCHAR(40) NULL` | `AIDAP-<year>-<PaymentID>` set at approval |
| `Payments` | `PeriodStart` | `DATE NULL` | plan period printed on the invoice |
| `Payments` | `PeriodEnd` | `DATE NULL` | plan period printed on the invoice |
| `Users` | `AdminOtpHash` | `NVARCHAR(128) NULL` | Super Admin OTP digest (server-side) |
| `Users` | `AdminOtpIssuedAt` | `DATETIME2 NULL` | OTP issue time |
| `Users` | `AdminOtpFailedAttempts` | `INT NOT NULL DEFAULT 0` | wrong-code counter |
| `Users` | `AdminOtpLockoutUntil` | `DATETIME2 NULL` | 15-minute lock after 5 wrong codes |

(`PasswordResetTokens.Attempts/UsedAt`, `EmailVerificationTokens.Attempts/UsedAt` and
`Datasets.InsightsEnabled/LastInsightScanAt` use the same guarded pattern and are skipped
when they already exist.) To see them after the restart:

```bash
sqlcmd -S 127.0.0.1,1433 -U $SA_USER -C -d $DB_NAME -Q "SELECT OBJECT_NAME(object_id) AS tbl, name FROM sys.columns WHERE name IN ('InvoiceNumber','PeriodStart','PeriodEnd','AdminOtpHash','AdminOtpIssuedAt','AdminOtpFailedAttempts','AdminOtpLockoutUntil')"
```

Expected: 7 rows.

**New / changed environment variables** (all described in `.env.example`):
`MAIL_FROM` (verified Brevo sender, e.g. `noreply@aidataanalystpro.co.uk`),
`INVOICE_COMPANY_NAME`, `INVOICE_COMPANY_ADDRESS`, `INVOICE_COMPANY_TAX_ID` (invoice seller
details, optional), `RATE_LIMITS_ENABLED` (default true), `TRUST_CLOUDFLARE_IP` (default
false). Existing values (`SECRET_KEY`, `DATASET_ENCRYPTION_KEY`) stay exactly as they are.

**Dependencies** — `requirements.txt` floors were raised to versions without published
advisories, so `pip install -r requirements.txt` WILL upgrade, among others, Flask (3.1.3+),
Werkzeug (3.1.9+), waitress (3.0.1+), requests, urllib3, pyarrow (23.0.1+), lxml (6.1+,
major) and Pillow (13+, major). The release was tested on exactly those versions.

**Behaviour to know about** — per-IP rate limits answer HTTP 429 after, for example, 10
failed logins from one address in 5 minutes or 5 Super Admin password attempts in 15
minutes (counters reset when the service restarts). Client IPs in audit logs now come only
from Nginx (see section 2b), not from a header the visitor can set.

---

`sqlcmd` is `/opt/mssql-tools18/bin/sqlcmd` (or `/opt/mssql-tools/bin/sqlcmd`). With
ODBC Driver 18 tools add `-C` (trust the local self-signed certificate). Each command
below prompts for the password if you leave out `-P`.

---

## 1. Before you deploy: backups

### 1a. SQL Server full backup (.bak)

SQL Server writes the file itself, so the folder must be writable by the `mssql` user:

```bash
sudo mkdir -p /var/opt/mssql/backup && sudo chown mssql:mssql /var/opt/mssql/backup
sqlcmd -S 127.0.0.1,1433 -U $SA_USER -C -Q "BACKUP DATABASE [$DB_NAME] TO DISK = N'/var/opt/mssql/backup/${DB_NAME}_$STAMP.bak' WITH COPY_ONLY, INIT, CHECKSUM, STATS = 10"
sqlcmd -S 127.0.0.1,1433 -U $SA_USER -C -Q "RESTORE VERIFYONLY FROM DISK = N'/var/opt/mssql/backup/${DB_NAME}_$STAMP.bak' WITH CHECKSUM"
sudo cp /var/opt/mssql/backup/${DB_NAME}_$STAMP.bak $BK/
```

Expected: `BACKUP DATABASE successfully processed ...` and
`The backup set on file 1 is valid.`

### 1b. Uploaded files and .env

```bash
cd $APP_DIR
tar czf $BK/uploads.tgz static/uploads static/images/profiles   # payment screenshots, avatars
cp .env $BK/env.backup && chmod 600 $BK/env.backup
git rev-parse HEAD > $BK/previous_commit.txt                     # needed for rollback
```

### 1c. DATASET_ENCRYPTION_KEY — separate, offline copy

Every stored dataset is encrypted with keys wrapped by `DATASET_ENCRYPTION_KEY`. A database
backup is useless without it, and it must not be stored next to the database backup.

```bash
grep '^DATASET_ENCRYPTION_KEY=' $APP_DIR/.env | sha256sum   # fingerprint only, safe to note down
```

Make sure the key itself is already in your password manager / offline note, and that the
fingerprint matches what you recorded last time. Never change this key on a server that
already holds datasets.

Copy `$BK` off the VM (e.g. `scp -r` to your PC or Object Storage) before continuing.

---

## 2. Update the code

```bash
cd $APP_DIR
git fetch origin
git status                     # must be clean; .env is ignored by git
git checkout main && git pull --ff-only origin main
source venv/bin/activate
pip install -r requirements.txt
python -m compileall -q . -x 'venv|\.git' && echo COMPILE_OK
```

Check `.env` against `.env.example` for new variables (lists every variable the code reads):

```bash
diff <(grep -oE '^[A-Z0-9_]+=' .env.example | sort -u) <(grep -oE '^[A-Z0-9_]+=' .env | sort -u)
```

Production must have at least:

```
SECRET_KEY=<existing value, unchanged>
FORCE_SECURE_COOKIES=true
APP_ENV=production
PUBLIC_BASE_URL=https://aidataanalystpro.co.uk
SUPPORT_EMAIL=<support address>
DATASET_ENCRYPTION_KEY=<existing value, unchanged>
UPLOAD_ASYNC_THRESHOLD_MB=50
GOOGLE_CLIENT_ID=... / GOOGLE_CLIENT_SECRET=...   (for Continue with Google)
ALLOW_SQLITE_FALLBACK=false
MAIL_FROM=noreply@aidataanalystpro.co.uk          (a sender verified in Brevo)
INVOICE_COMPANY_NAME=<legal/trading name>          (optional: _ADDRESS, _TAX_ID)
RATE_LIMITS_ENABLED=true
TRUST_CLOUDFLARE_IP=false                          (true only if the VM accepts Cloudflare traffic alone)
TRUSTED_PROXY_COUNT=                               (empty: wsgi.py already trusts the local Nginx)
```

### 2b. Nginx and systemd: check, do not change unless different

The app takes the visitor's IP from the ONE proxy hop on `127.0.0.1` (Waitress
`trusted_proxy=127.0.0.1`, `trusted_proxy_count=1`). The site block must pass these headers
and allow 500 MB uploads:

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;   # or $remote_addr
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host  $host;
    client_max_body_size 500M;
    proxy_read_timeout 300s;
}
```

```bash
sudo nginx -T 2>/dev/null | grep -nE "proxy_pass|X-Forwarded-For|X-Forwarded-Proto|client_max_body_size"
```

The one systemd unit (`/etc/systemd/system/$SERVICE.service`) should look like this; there
must be no second unit, cron job or `screen` session running `run.py`/`wsgi.py`:

```ini
[Unit]
Description=AI Data Analyst Pro (Waitress)
After=network.target mssql-server.service

[Service]
User=ubuntu
WorkingDirectory=/opt/AI_Data_Analyst_Pro
ExecStart=/opt/AI_Data_Analyst_Pro/venv/bin/python wsgi.py
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
systemctl list-units --type=service | grep -i -E "aidap|analyst"   # exactly one
pgrep -af "wsgi.py|run.py"                                         # exactly one process
```

---

## 3. Dataset encryption migration — on a COPY first

`python -m utils.dataset_vault migrate` moves any remaining plaintext dataset tables into
encrypted storage and empties the plaintext table. It is idempotent (already-encrypted
tables are skipped). Rehearse it on a restored copy of the database first.

### 3a. Restore the backup as a copy database

```bash
sqlcmd -S 127.0.0.1,1433 -U $SA_USER -C -Q "RESTORE FILELISTONLY FROM DISK = N'/var/opt/mssql/backup/${DB_NAME}_$STAMP.bak'"
```

Note the two `LogicalName` values (data and log), then:

```bash
sqlcmd -S 127.0.0.1,1433 -U $SA_USER -C -Q "RESTORE DATABASE [${DB_NAME}_migtest] FROM DISK = N'/var/opt/mssql/backup/${DB_NAME}_$STAMP.bak' WITH MOVE N'<data logical name>' TO N'/var/opt/mssql/data/${DB_NAME}_migtest.mdf', MOVE N'<log logical name>' TO N'/var/opt/mssql/data/${DB_NAME}_migtest_log.ldf', STATS = 10"
```

### 3b. Run the migration against the copy

The app service keeps running against the real database; only this one command points
at the copy (environment variables override `.env`):

```bash
cd $APP_DIR && source venv/bin/activate
DB_NAME=${DB_NAME}_migtest python -m utils.dataset_vault migrate
DB_NAME=${DB_NAME}_migtest python -m utils.dataset_vault migrate     # second run: everything skipped
```

Spot-check the copy: pick a dataset, compare its row count before/after and open it in a
Python shell with `DB_NAME=${DB_NAME}_migtest` (`from database.connection import run_query`).
If anything fails, stop here: the real database is untouched.

Drop the copy afterwards:

```bash
sqlcmd -S 127.0.0.1,1433 -U $SA_USER -C -Q "DROP DATABASE [${DB_NAME}_migtest]"
```

### 3c. Run it on the real database

```bash
sudo systemctl stop $SERVICE           # no uploads while migrating
python -m utils.dataset_vault migrate
```

---

## 4. Restart

```bash
sudo systemctl restart $SERVICE
sudo systemctl status $SERVICE --no-pager
journalctl -u $SERVICE -n 80 --no-pager
```

In the log, there must be NO `CONFIG WARNING` line about `PUBLIC_BASE_URL` (it is printed
when the variable is missing, not https, or points at localhost in production), no
`EMAIL CONFIG WARNING` line (missing SMTP login, missing `MAIL_FROM`, or the Brevo SMTP
login used as From), and no database initialisation error. Then run the schema check from
section 0 (7 rows). `sudo nginx -t && sudo systemctl reload nginx` only if the
Nginx config changed.

---

## 5. Smoke tests on https://aidataanalystpro.co.uk

```bash
curl -fsS https://aidataanalystpro.co.uk/health ; echo
curl -sI https://aidataanalystpro.co.uk/auth/login | head -n 1                # HTTP/2 200 (or HTTP/1.1 200)
curl -sI https://aidataanalystpro.co.uk/ | grep -i strict-transport-security  # present when FORCE_SECURE_COOKIES=true
curl -s  https://aidataanalystpro.co.uk/sitemap.xml | grep -m1 '<loc>'        # must start with https://aidataanalystpro.co.uk
curl -sI https://aidataanalystpro.co.uk/auth/google/login | grep -i '^location' # accounts.google.com, redirect_uri=https%3A%2F%2Faidataanalystpro.co.uk%2Fauth%2Fgoogle%2Fcallback
```

In a browser:

1. Log in with an existing account (password + emailed code when required).
2. Super Admin: `/admin/login`, password, then the emailed OTP.
3. Upload a small CSV (< 1 MB) as a free user: answered at once. Ask AI a question about it
   ("total sales by region") and compare with the file.
4. Ask AI "what's the weather today?": must be refused politely.
5. "Forgot password" on a test account: open the email and check that every link starts with
   `https://aidataanalystpro.co.uk` (not 127.0.0.1 / localhost) and the support address is right.
6. Continue with Google on the login page.
7. Super Admin → Payments: approve a test payment, then download its Invoice PDF (number
   `AIDAP-<year>-<id>`, PKR 1,499.00, period, your company details); the user downloads the
   same PDF from Receipts, and the "Payment approved" email links to it.
8. Super Admin → Audit logs: your own login shows your real public IP, not `127.0.0.1`.
9. Exports: on a dataset, download CSV, Excel, PDF, Word and PowerPoint and open each.
10. Headers: `curl -sI https://aidataanalystpro.co.uk/ | grep -iE "content-security-policy|x-frame-options|cross-origin-opener"`
    (the CSP must not mention fonts.gstatic.com).

Do NOT test the rate limit from your own connection on production with the real login:
10 failed attempts lock your IP out of login for 5 minutes (restart the service to clear it).

---

## 6. Rollback

Use when the smoke tests fail.

```bash
sudo systemctl stop $SERVICE
cd $APP_DIR
git checkout "$(cat $BK/previous_commit.txt)"
source venv/bin/activate && pip install -r requirements.txt
```

Restore the database only if the new version changed data (e.g. the migration ran and the
old code must read the old layout):

```bash
sqlcmd -S 127.0.0.1,1433 -U $SA_USER -C -Q "ALTER DATABASE [$DB_NAME] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; RESTORE DATABASE [$DB_NAME] FROM DISK = N'/var/opt/mssql/backup/${DB_NAME}_$STAMP.bak' WITH REPLACE, STATS = 10; ALTER DATABASE [$DB_NAME] SET MULTI_USER"
```

Restore files and settings:

```bash
cd $APP_DIR
tar xzf $BK/uploads.tgz
cp $BK/env.backup .env && chmod 600 .env
sudo systemctl restart $SERVICE
curl -fsS https://aidataanalystpro.co.uk/health ; echo
```

Later, return to the current release with `git checkout main && git pull --ff-only`.

The new columns from section 0 can stay: the previous code never reads them, so no
schema rollback is needed. `pip install` does not downgrade packages; the previous code
runs on the newer versions too (its floors are lower), so leave them.

Note: restoring the .bak discards everything written after the backup (new users,
uploads, payments). Prefer a code-only rollback when the data is fine.
