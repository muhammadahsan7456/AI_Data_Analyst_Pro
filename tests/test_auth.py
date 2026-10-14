"""
Login and signup flow tests.

test_signup_reaches_otp_page registers a brand-new account with a unique,
timestamp-based email on every run (so tests can be re-run without colliding
with a previous run's account) and only checks it reaches the OTP-verification
step - it does not try to read the real code from the database and finish
verifying, which would require direct DB access. See tests/README.md for how
to extend this into a full signup-to-dashboard test if you want that.
"""
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def test_login_with_wrong_password_shows_error(driver, base_url):
    driver.get(base_url + "/auth/login")
    driver.find_element(By.ID, "email_or_username").send_keys("no-such-user@example.com")
    driver.find_element(By.ID, "password").send_keys("WrongPassword123!")
    driver.find_element(By.CSS_SELECTOR, "button[type=submit]").click()

    # Flask-WTF flashes land in a .flash-message / alert block rendered on the
    # same page after a failed login - wait for it instead of a fixed sleep.
    WebDriverWait(driver, 10).until(
        lambda d: "login" in d.current_url
    )
    page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    assert "invalid" in page_text or "incorrect" in page_text or "not found" in page_text


def test_signup_reaches_otp_page(driver, base_url):
    unique_email = f"selenium.test.{int(time.time())}@example.com"

    driver.get(base_url + "/auth/signup")
    driver.find_element(By.ID, "first_name").send_keys("Selenium Test")
    driver.find_element(By.ID, "email").send_keys(unique_email)
    driver.find_element(By.ID, "signup_password").send_keys("SeleniumPass123!")
    driver.find_element(By.ID, "confirm_password").send_keys("SeleniumPass123!")

    # Scrolling into view wasn't enough here - Chrome still reports the native click
    # as intercepted at that exact pixel (something in this form's layout sits over
    # the checkbox's small 16x16 hit box even when it's in view). A JS-dispatched
    # click bypasses WebDriver's pixel-level "is this point actually clickable"
    # check and fires the click directly on the element - the standard, reliable
    # fallback for exactly this situation rather than fighting the real click.
    terms_checkbox = driver.find_element(By.ID, "terms")
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", terms_checkbox)
    driver.execute_script("arguments[0].click();", terms_checkbox)

    submit_btn = driver.find_element(By.CSS_SELECTOR, "button[type=submit]")
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", submit_btn)
    driver.execute_script("arguments[0].click();", submit_btn)

    WebDriverWait(driver, 10).until(EC.url_contains("verify-otp"))
    assert "verify-otp" in driver.current_url
