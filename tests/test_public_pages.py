"""
Smoke tests for the public-facing pages - no login required. These check that the
core pages actually load, have the right title, and show their key call-to-action,
which is usually enough to catch "the page is broken/blank/500ing" regressions fast.
"""
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def test_homepage_loads(driver, base_url):
    driver.get(base_url + "/")
    assert "AI Data Analyst Pro" in driver.title

    heading = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "h1.hero-title"))
    )
    assert "Transform Enterprise Datasets" in heading.text


def test_login_page_has_required_fields(driver, base_url):
    driver.get(base_url + "/auth/login")
    assert driver.find_element(By.ID, "email_or_username").is_displayed()
    assert driver.find_element(By.ID, "password").is_displayed()
    assert driver.find_element(By.LINK_TEXT, "Forgot Password?").is_displayed()


def test_signup_page_has_required_fields(driver, base_url):
    driver.get(base_url + "/auth/signup")
    assert driver.find_element(By.ID, "first_name").is_displayed()
    assert driver.find_element(By.ID, "email").is_displayed()
    assert driver.find_element(By.ID, "signup_password").is_displayed()
    assert driver.find_element(By.ID, "confirm_password").is_displayed()
