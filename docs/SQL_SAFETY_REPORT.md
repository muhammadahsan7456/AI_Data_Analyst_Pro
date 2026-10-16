# SQL Safety Report — Task 4 (Consistency & Safety Update)

Date: 2026-10-16

This report covers two things:

1. **What was changed** — removing SQL from everything the end user's browser can see
   (UI and API responses), per the master prompt's explicit instruction.
2. **What was found but NOT changed** — the deeper SQL-generation safety architecture
   (DB permissions, query validation, tenant isolation). These are real risks worth
   fixing, but they are architectural changes with real blast radius, not copy/display
   fixes, so per the master prompt's "ask before anything risky" rule, they are
   documented here as a **separate, approval-needed next job**, not silently patched.

## 1. SQL removed from user-facing UI and API (done)

| Location | What it exposed | Fix |
|---|---|---|
| `templates/chat.html:161-174` | A "Local T-SQL Query" panel with the raw generated SQL and a "Copy SQL" button, shown after every Ask AI question | Block removed entirely |
| `templates/chat.html` "Applied Filters" badges | Raw SQL WHERE-clause fragments (e.g. `TRY_CAST([OrderDate] AS DATE) BETWEEN ? AND ?`) shown as filter chips | `frontend/routes.py` `chat()` now builds `applied_filters` from the query plan's own `column`/`value` fields (e.g. "OrderDate: 2026-01-01 to 2026-03-31") instead of `sql_clause` |
| `frontend/routes.py` `chat()` AJAX JSON responses | `"sql"` key with the raw SQL string, and a `"query_plan"` key containing the full plan (including every filter's raw `sql_clause`) | Both keys removed from all `jsonify()` calls in `chat()` |
| `frontend/routes.py` `chat()` `render_template()` calls | `sql=ai_result["sql"]` and `query_plan=query_plan` passed into the template | Both removed; template no longer references either |
| `frontend/routes.py` `/api/query-history` (`api_query_history`) | `"sql": r[3]` — the raw SQL for every past question, reachable by any logged-in user for their own history even though no page currently links to it | `"sql"` key removed from the response |
| `templates/terms.html:117` | Told users to "verify generated T-SQL logic via the interactive SQL Preview bar" — a feature that no longer exists | Reworded to reference the answer/summary/table instead |

**Confirmed**: no endpoint accepts raw SQL text from the client (`grep` for
`request.form/json/values/args.get("sql")` across the whole repo returned nothing) —
there was never a client-to-server SQL injection surface here, only server-to-client
SQL *exposure*, which is what this task removes.

**Server-side SQL logging is untouched and intentional**: `session["last_sql"]`
(`frontend/routes.py`, set in `chat()`) still stores the SQL for the currently
displayed result, used by `/download-chart` to regenerate a chart from the same
query without re-running SQL generation. This never reaches a JSON response or
template now — it's purely server-side session state, consistent with "keep SQL
only in server logs/admin-only audit log."

## 2. Error messages: stopped leaking raw database/driver text (done)

`utils/error_translator.py`'s `format_user_friendly_error()` (used by the Ask AI
error path) had two fallback branches that echoed a cleaned-but-still-raw version of
the exception string back to the browser — the final fallback had **no length limit
or content filtering beyond stripping `(...)`/`[...]`**, so any error shape the
author didn't anticipate (e.g. a driver message with a table name outside brackets)
would reach the user verbatim. Both fallback branches now log the raw error
server-side (`print(...)`) and return a fixed generic message instead.

Separately, 12 API routes in `frontend/routes.py` (favorites, insights toggle,
AI notifications, dataset quality/insights, query history delete/clear, scheduled
reports, alert rules, dataset health) returned `str(e)`/`str(err)` directly in their
JSON `"error"` field with **no sanitization at all** — these all guard
parameterized/fixed queries (not AI-generated SQL), so the risk was lower than the
Ask AI path, but any unexpected exception (DB connection drop, constraint violation,
timeout) would still have shown raw exception text to the browser. All 12 now log
the real error server-side and return a fixed generic message.

The existing global 500 handler (`frontend/routes.py`, around line 3160) already did
this correctly with a comment explaining why — these 12 routes bypassed it because
they catch and return their own JSON before the request would ever reach that
handler. The fix extends the same discipline to them.

## 3. Found but NOT fixed — SQL generation safety architecture (needs owner decision)

### 3a. Database login used for AI-generated queries

`database/connection.py` (`get_connection_string()`, `get_connection()`) builds
**one** SQL Server connection from `.env` (`DB_SERVER`/`DB_NAME`/`DB_USER`/
`DB_PASSWORD`, or Windows `Trusted_Connection`) and uses it for *everything*: user
auth tables, dataset metadata, schema migrations (`init_db()` runs `CREATE TABLE`/
`ALTER TABLE`), dataset deletion (`DROP TABLE`), **and** every AI-generated SELECT.

**Risk**: there is no database-level read-only boundary. The only thing stopping an
AI-generated query from running a destructive statement is the application-layer
keyword blocklist described below (3b). If that blocklist is ever bypassed, the DB
permissions do not provide a second line of defense — the same login that can
`DROP TABLE` for the app's own migrations can `DROP TABLE` for a malformed
AI-generated query too.

**Recommendation**: create a second, restricted SQL Server login with `SELECT`-only
permission (ideally scoped to just the dynamically-created dataset tables, not the
`Users`/`Payments`/`Reviews` etc. system tables) and use it specifically for
`run_query()` calls that execute AI-generated SQL. This is a real infrastructure
change (new DB login, permission grants, code path to select which connection to
use) — not something to do silently inside a "remove SQL from the dashboard" task.

### 3b. SQL validation approach: keyword blocklist, not a real parser

Two independent checks, both substring/regex matching on the raw SQL text — neither
uses a real SQL parser or AST:

- `ai/gemini.py` `clean_sql_response()` (lines 826-865): truncates at the first `;`
  (blocks classic stacked-statement injection), then rejects the string if a
  `\bKEYWORD\b` regex matches any of `DROP, DELETE, TRUNCATE, UPDATE, INSERT, ALTER,
  EXEC, EXECUTE, GRANT, REVOKE, CREATE, MERGE`.
- `ai/gemini.py` `execute_sql_with_retry()` (lines 1041-1044): a second check
  immediately before execution — `any(f" {cmd} " in f" {upper_sql} " for cmd in
  forbidden) or not upper_sql.lstrip().startswith("SELECT")`. This one checks for
  the keyword surrounded by **literal space characters**, which is weaker than the
  regex above it (a keyword followed by `(` or a newline instead of a space would
  not match).

**Risk**: keyword/substring blocklists are a known-weak pattern. Neither check
accounts for SQL comments splitting a keyword apart, and neither blocks `UNION`,
arbitrary `JOIN`/`FROM` targets, `INFORMATION_SCHEMA`/`sys.*` catalog access,
`WAITFOR` (blind time-based probing), or `OPENROWSET`/`BULK` (potential
file/remote access depending on server configuration) — the blocklist was designed
to stop *write* operations, not to constrain *read* scope at all.

**Recommendation**: replace or supplement the blocklist with a real SQL parser
(e.g. `sqlglot` or similar) that can assert the query is a single `SELECT`
statement touching only the expected table(s) — see 3c below, this is the same fix
for both problems. This is a meaningful engineering task (parser integration,
testing against the existing query-generation test surface), not a quick patch.

### 3c. Tenant/user data isolation for AI-generated SQL: prompt-only, not code-enforced

The table name the AI should query is selected correctly and safely — `chat()`
resolves `table_name` via an authenticated, user-scoped dataset lookup before any AI
call. **But** once the LLM generates SQL text, nothing re-checks that the generated
`FROM`/`JOIN` actually stayed within that one table. The table name reaches the
model only as prompt instruction text (`ai/sql_agent.py` `generate_sql_prompt()`,
e.g. `"Table Name: [{table_name}]"`) — a request the model is trusted to follow, not
a constraint enforced in code. Neither `clean_sql_response()` nor the pre-execution
check in `execute_sql_with_retry()` extracts the table(s) actually referenced in the
returned SQL and compares them against `table_name`.

**Risk**: a sufficiently adversarial question could in principle lead the model to
generate SQL referencing a different table (another user's dataset table, or a
system view like `sys.tables`) via `UNION SELECT` or a direct `FROM` substitution.
The keyword blocklist (3b) would not catch this, since it's a syntactically valid
`SELECT`. The query would then run via `run_query()` under the shared DB login
(3a), which plausibly has broad read access across the database.

**Good counter-example already in the codebase**: the multi-dataset cross-join
feature (`frontend/routes.py` `multi_dataset_analyze()`) does this correctly — it
resolves dataset IDs to table names via a parameterized, user-scoped query
*before* calling the AI, so the tables handed to the AI layer are already
constrained server-side. The single-table Ask AI path gets the *selection* right
the same way, but has no equivalent check on the AI's *output*.

**Recommendation**: after SQL generation, parse the returned SQL (same parser as
3b) and assert every table/view it references is exactly `table_name` — reject and
retry (the existing retry loop already handles rejected SQL) otherwise.

## 4. Recommended next job (not started)

Items 3a-3c are related and should likely be done together as one focused
engineering task: add a real SQL parser as the single source of truth for "is this
SQL safe," use it both to enforce single-table scope (3c) and to replace the
keyword blocklist (3b), and separately provision a restricted read-only DB login
for AI-generated query execution (3a) as defense in depth. This needs:

- Your explicit approval to start (per the master prompt's safety rules — this
  touches the AI query execution path, which is core, trusted functionality).
- A test plan covering the existing fast-path and LLM-generated query patterns, to
  confirm the parser doesn't reject legitimate queries the blocklist currently
  allows.
- A decision on whether to provision the restricted DB login now or treat it as a
  later hardening step after the parser-based validation lands.

None of this was done as part of this task — this report exists so you have the
information to decide whether/when to approve it as separate work.
