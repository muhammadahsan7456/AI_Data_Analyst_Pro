# Baseline — captured before the consistency-and-safety-update branch

Date: 2026-10-16
Branch: `fix/consistency-and-safety-update` (created from `main` @ `807cbf5`)
Database: the real deployed RDS database (local `.env` and the production server's
`.env` point at the same instance — there is no separate staging DB for this project).

## 1. Existing automated test suite

```
tests/test_auth.py::test_login_with_wrong_password_shows_error PASSED
tests/test_auth.py::test_signup_reaches_otp_page PASSED
tests/test_public_pages.py::test_homepage_loads PASSED
tests/test_public_pages.py::test_login_page_has_required_fields PASSED
tests/test_public_pages.py::test_signup_page_has_required_fields PASSED
5 passed in 83.52s
```

## 2. Manual/scripted flow checks (local dev server, real DB, disposable test accounts)

| Check | Result |
|---|---|
| Home loads | PASS (HTTP 200) |
| Register -> reaches OTP page | PASS |
| Account row created in DB | PASS |
| Dashboard loads when logged in | PASS (HTTP 200) |
| Upload CSV (real file, real pipeline) | PASS ("Ingested Successfully", table + rows created) |
| Dataset row created in DB | PASS |
| Ask AI returns an answer | PASS (non-empty result) |
| Chart container renders | PASS |
| Export buttons (CSV/PDF) present | PASS |
| Demo page loads | PASS (HTTP 200) |

All disposable test accounts/datasets/tables created during this baseline run were
deleted afterward (UserIDs 160 and the one from baseline_check2.py - both fully
cleaned up, including their physical SQL tables).

## 3. Row counts (existing real data, for before/after comparison)

```
Users:      40
Datasets:   54
QueryLogs:  1029
```

## 4. Notes for this pass

- Local dev server does not auto-reload templates (`TEMPLATES_AUTO_RELOAD` is off/follows
  `DEBUG=False`) - any template change requires restarting the local server process before
  the next check. This is a recurring, already-known characteristic of this project, not
  specific to this task.
- This project currently has **no dedicated staging database** - the local dev environment's
  `.env` and the production server's `.env` both connect to the same RDS instance. All test
  accounts used for verification are therefore created directly against real production data
  and must be deleted afterward every time (the established pattern used throughout this
  project's prior QA work too).
