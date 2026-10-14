"""
Shared pytest fixtures for the Selenium test suite.

- BASE_URL: which server to test against. Defaults to the local dev server
  (http://127.0.0.1:5000) so running tests never touches production data.
  Override with an environment variable to point at production instead:
      set TEST_BASE_URL=https://aidataanalystpro.co.uk   (Windows cmd)
      $env:TEST_BASE_URL="https://aidataanalystpro.co.uk" (PowerShell)

- HEADLESS: runs Chrome with no visible window by default (set HEADLESS=false
  to watch the browser while debugging a test).
"""
import os
import pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE_URL = os.getenv("TEST_BASE_URL", "http://127.0.0.1:5000").rstrip("/")


@pytest.fixture
def base_url():
    return BASE_URL


@pytest.fixture
def driver():
    options = Options()
    if os.getenv("HEADLESS", "true").lower() != "false":
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--disable-gpu")

    # Selenium 4.6+ downloads/manages the matching chromedriver automatically -
    # no separate webdriver-manager package or manual driver download needed.
    drv = webdriver.Chrome(options=options)
    drv.implicitly_wait(5)  # small implicit wait as a safety net alongside explicit waits
    yield drv
    drv.quit()
