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

## 5. Final regression pass - after Tasks 1-7 (2026-10-17)

```
tests/test_auth.py::test_login_with_wrong_password_shows_error PASSED
tests/test_auth.py::test_signup_reaches_otp_page PASSED
tests/test_demo_sandbox.py::test_demo_sandbox_requested_count_matches_shown_rows PASSED
tests/test_demo_sandbox.py::test_demo_sandbox_top5_insight_percentage_matches_rows PASSED
tests/test_public_pages.py::test_homepage_loads PASSED
tests/test_public_pages.py::test_login_page_has_required_fields PASSED
tests/test_public_pages.py::test_signup_page_has_required_fields PASSED
7 passed in 51.54s
```

Full manual/scripted flow re-check (register -> OTP -> dashboard -> upload CSV -> Ask
AI in English and Roman Urdu -> chart renders -> no SQL panel visible -> export buttons
present -> demo page all 6 examples -> all 9 other major pages return 200): 21/23 checks
passed on the first run; the 2 that initially failed (first Ask AI question and its
chart) were re-verified in isolation and confirmed to be a test-script timing issue (the
very first AI query in a fresh session has a one-time ~6-8s cold-start cost, same pattern
documented in `docs/PERFORMANCE_MEASUREMENT.md`), not a real regression - a longer wait
on that single check passed cleanly (6.11s, success=True, chart_spec present).

Mobile (360px) horizontal-overflow check and browser console-error check: clean on every
page touched by Tasks 1-7 (home, testimonials, privacy, pricing, demo, features, docs,
faq, help, about, terms).

Row counts (same real DB as Section 3 above, re-confirmed after cleaning up every test
account/dataset created during this engagement, including 10 leftover accounts from
repeated `pytest` runs that the pre-existing `test_signup_reaches_otp_page` test doesn't
clean up after itself):

```
Users:      40  (unchanged)
Datasets:   54  (unchanged)
QueryLogs:  1029 (unchanged)
Reviews:    6   (unchanged row count; ReviewID 1's text was intentionally edited in
                 Task 2 to remove a fabricated "0.005 milliseconds" claim - see that
                 task's commit message)
```

No existing user data, datasets, or query history was lost or altered beyond that one
documented review-text correction.
