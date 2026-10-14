# Selenium test suite

Browser-driven tests for AI Data Analyst Pro, using Selenium + pytest.

## One-time setup

```bash
# from the project root, with your virtualenv activated
pip install -r requirements-test.txt
```

You also need **Google Chrome** installed. Selenium 4.6+ downloads and manages
the matching chromedriver automatically the first time a test runs - no
separate driver download needed.

## Running the tests

Start the app locally first (in a separate terminal):

```bash
python run.py
```

Then, in another terminal:

```bash
pytest                      # run everything
pytest -v                   # verbose - see each test name as it runs
pytest tests/test_auth.py   # run one file
pytest -k signup            # run only tests whose name contains "signup"
```

By default tests run **headless** (no visible browser window) against your
**local** server at `http://127.0.0.1:5000`. To watch the browser while a test
runs (handy for debugging):

```bash
# Windows PowerShell
$env:HEADLESS="false"; pytest -v

# Windows cmd
set HEADLESS=false && pytest -v
```

To point the suite at production instead of local (read the warning below first):

```bash
# PowerShell
$env:TEST_BASE_URL="https://aidataanalystpro.co.uk"; pytest -v
```

> **Be careful running against production.** `test_signup_reaches_otp_page`
> really creates a new account every time it runs (a fresh timestamped email,
> so it never collides with a previous run) - that's harmless against your
> local dev database, but it will leave real rows in the production database
> if you point `TEST_BASE_URL` at it. Prefer testing locally; only point at
> production for a one-off spot-check, and clean up any accounts it creates
> afterward (`WHERE Email LIKE 'selenium.test.%'`).

## What's here

- `conftest.py` - shared pytest fixtures: the `driver` fixture (creates and
  quits a Chrome WebDriver per test) and the `base_url` fixture (reads
  `TEST_BASE_URL`, defaults to the local server).
- `test_public_pages.py` - pages that don't need login: homepage, and that
  the login/signup forms actually render their required fields.
- `test_auth.py` - a negative login test (wrong password shows an error) and
  a full signup test (fills the form, submits, confirms it reaches the
  OTP-verification page).

## Writing a new test

Every test function takes `driver` and/or `base_url` as arguments - pytest
supplies them automatically from `conftest.py`. The general shape:

```python
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def test_something(driver, base_url):
    driver.get(base_url + "/some/path")
    driver.find_element(By.ID, "some-field").send_keys("value")
    driver.find_element(By.CSS_SELECTOR, "button[type=submit]").click()

    # wait for the result instead of a fixed time.sleep() - sleeping a fixed
    # amount is either too slow (wastes time every run) or too fast (flaky
    # on a slower machine/connection)
    WebDriverWait(driver, 10).until(EC.url_contains("/expected-page"))
    assert "/expected-page" in driver.current_url
```

**If a `.click()` throws `ElementClickInterceptedException`**, Chrome's
native click requires the exact pixel to be unobstructed, which this app's
layout doesn't always guarantee even after scrolling the element into view.
The standard fallback used in `test_auth.py` is a JS-dispatched click, which
bypasses that pixel check:

```python
el = driver.find_element(By.ID, "some-field")
driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
driver.execute_script("arguments[0].click();", el)
```

## Extending to a full signup-to-dashboard test

`test_signup_reaches_otp_page` stops at the OTP page because completing
verification needs the real 6-digit code, which only exists in the database
(not on the page - it's emailed). To go further, read it directly from the
database right after signup, the same way this project's own Playwright test
scripts did throughout development:

```python
from database.connection import get_db_cursor

with get_db_cursor() as cur:
    cur.execute(
        "SELECT TOP 1 OTPCode FROM PendingVerifications WHERE Email = ? ORDER BY CreatedAt DESC",
        (unique_email,)
    )
    otp = cur.fetchone()[0]
# then fill #otp_code on the verify-otp page with `otp` and submit
```

(Check `database/queries.py` for the exact table/column names the signup OTP
flow actually uses before copying that query - it may differ slightly from
the password-reset token table.)
