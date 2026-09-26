# AI Data Analyst Pro

A Flask-based SaaS application that lets users upload datasets (CSV, Excel, JSON, XML, TSV, Parquet, Feather),
ask questions about them in natural language via an AI SQL agent, and get back interactive charts,
executive summaries, forecasts, and exportable reports (PDF/Word/PowerPoint/Excel).

## Running the app

- `run.py` - the actual Flask application (routes, blueprints, request hooks). Can be run directly
  for local development (`python run.py`); falls back to Flask's dev server if Waitress isn't installed.
- `wsgi.py` - production entrypoint (`python wsgi.py`), imports the app from `run.py` and serves it with
  the Waitress WSGI server. This is what the Dockerfile runs.
- `app.py` - a separate, standalone CLI script (not the web app) for basic terminal-based testing of the
  database/AI/chart modules directly, independent of Flask.

## Project structure

- `run.py` / `wsgi.py` - Flask application entrypoints (see above)
- `database/` - `connection.py` (SQL Server/pyodbc connection handling, schema init) and `queries.py`
  (centralized parameterized SQL query builders)
- `ai/` - AI SQL agent, natural-language query planning, semantic column matching, data quality/health
  scoring, anomaly detection, forecasting, and executive summary generation
- `auth/` - user authentication (signup, login, OTP email verification, password reset, sessions)
- `admin/` - Super Admin console (user management, role assignment, payments, audit logs)
- `frontend/` - the main application routes (dashboard, upload, chat/Ask AI, dataset views, exports)
- `templates/` - Jinja2 HTML templates for all pages
- `static/` - CSS, JS, and image assets
- `uploads/` - multi-format file ingestion pipeline (parsing, schema inference, batch insertion)
- `visualization/` - chart generation (Plotly interactive specs for the web UI, Matplotlib for
  document exports and the downloadable chart image)
- `utils/` - shared helpers: caching, encryption, logging, alert rules, scheduled reports, exporters
  (PDF/Word/PPT/Excel), subscription handling

## Configuration

Copy your own values into a local `.env` file (never commit this file) with at minimum:

- `SECRET_KEY` - a strong random value (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`).
  If unset, the app generates a random one-time key at startup and logs a warning; sessions won't
  survive a restart in that case.
- `DB_SERVER`, `DB_NAME`, `DB_DRIVER`, `DB_TRUSTED_CONNECTION` (or `DB_USER`/`DB_PASSWORD`) - SQL Server
  connection details.
- `OPENROUTER_API_KEY` - used for AI question answering (via the OpenAI SDK against OpenRouter).
- `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL` - for OTP/notification emails.
- `DATASET_ENCRYPTION_KEY` - Fernet key used for field-level encryption of sensitive columns.
- `ALLOW_SQLITE_FALLBACK` - optional, defaults to disabled. Only set to `true` for local development
  if you want the app to fall back to a local SQLite database when SQL Server is unreachable; in
  production a SQL Server outage raises a real error instead.

## Admin access

There is no default/seeded admin account. Admin access is granted purely through the `Role` column on
the `Users` table (`SuperAdmin` or `Admin`). To grant admin access to an account, an existing SuperAdmin
must promote it from the Super Admin console (`/admin` &rarr; Manage Users &rarr; Change Role), or it can
be set directly in the database for the very first admin account on a fresh install.

## Dependencies

See `requirements.txt`. Notable choices:
- Data is stored in Microsoft SQL Server via `pyodbc` (raw parameterized SQL, no ORM).
- AI question-answering goes through the OpenAI SDK against OpenRouter (despite the module being named
  `ai/gemini.py` for historical reasons, it does not call Google's Gemini API directly).
- Charts are rendered as Plotly.js specs for interactive web display, and as Matplotlib PNGs for
  document exports and the "Download HD Chart" button.
