"""
Regression test for Task 6 (Consistency & Safety Update): the interactive demo
sandbox (/demo) previously claimed "Top 10" / "Last 5" / etc. while actually
rendering a different number of sample rows, and its insight text ("60% of
booking volume") didn't match what the rows actually showed. This asserts the
number requested in the generated SQL ("TOP N") always equals the number of
rows actually rendered in the sandbox table, for every use case.
"""
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

SANDBOX_CASES = ["top10", "count", "last", "revenue", "join", "urdu"]
# These two use cases return a single aggregate row (COUNT/SUM), not a "TOP N"
# list, so there is no N-requested-vs-N-shown comparison to make for them.
AGGREGATE_CASES = {"count", "revenue"}


def test_demo_sandbox_requested_count_matches_shown_rows(driver, base_url):
    driver.get(base_url + "/demo")

    for case in SANDBOX_CASES:
        btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, f"button[onclick*=\"runSandbox('{case}'\"]"))
        )
        btn.click()

        sql_text = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "sandbox-sql"))
        ).text
        row_count = len(driver.find_elements(By.CSS_SELECTOR, "#sandbox-tbody tr"))

        if case in AGGREGATE_CASES:
            assert row_count == 1, f"[{case}] aggregate case should render exactly 1 summary row, got {row_count}"
            continue

        match = re.search(r"TOP\s+(\d+)", sql_text, re.IGNORECASE)
        assert match, f"[{case}] expected a 'TOP N' clause in the displayed SQL, got: {sql_text!r}"
        requested_n = int(match.group(1))
        assert requested_n == row_count, (
            f"[{case}] SQL requests TOP {requested_n} but the sandbox table shows {row_count} rows"
        )


def test_demo_sandbox_top5_insight_percentage_matches_rows(driver, base_url):
    """The top10 case's insight claims a % of rows from a specific origin city -
    verify that percentage is actually correct for the rows shown, instead of a
    hardcoded number that can silently drift out of sync with the sample data."""
    driver.get(base_url + "/demo")
    driver.find_element(By.CSS_SELECTOR, "button[onclick*=\"runSandbox('top10'\"]").click()

    rows = WebDriverWait(driver, 10).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, "#sandbox-tbody tr") or False
    )
    origins = [row.find_elements(By.TAG_NAME, "td")[3].text.strip() for row in rows]
    khi_count = sum(1 for o in origins if o == "KHI")
    real_pct = round(100 * khi_count / len(origins))

    insight_text = driver.find_element(By.ID, "sandbox-takeaway-text").text
    match = re.search(r"(\d+)%", insight_text)
    assert match, f"Expected a percentage in the insight text, got: {insight_text!r}"
    claimed_pct = int(match.group(1))
    assert claimed_pct == real_pct, (
        f"Insight claims {claimed_pct}% KHI but {khi_count}/{len(origins)} rows "
        f"({real_pct}%) actually have origin KHI"
    )
