"""Unit tests for the Ask AI domain guardrail, typo correction and early deterministic patterns.

These run without a database or LLM key. End-to-end accuracy against real data is checked
by the QA battery described in docs/PROGRESS.md (Phase 6)."""
import pytest

from ai import gemini

COLS = ["RecordID", "OrderID", "OrderDate", "Region", "City", "Product", "Customer",
        "Status", "Quantity", "UnitPrice", "Sales", "Profit"]


@pytest.mark.parametrize("q", [
    "total sales by region",
    "top 5 products by profit",
    "how many orders were cancelled",
    "average unit price by product",
    "monthly sales trend",
    "which city has the highest sales",
    "compare total profit of North and South",
    "sab se zyada sales kis city mein hai",
    "kitne orders hain",
    "delivered orders ki total sales batao",
    "show me the first 10 rows",
    "who is the top customer by sales",
    "what is my total profit",
])
def test_in_scope_questions_are_not_refused(q):
    assert gemini.is_out_of_domain_question(q, COLS) is False


@pytest.mark.parametrize("q", [
    "what is the weather in Lahore today",
    "who is the prime minister of Pakistan",
    "write a python function to reverse a string",
    "tell me a joke",
    "what is the capital of France",
    "how are you",
    "explain quantum physics",
    "kal mausam kaisa hoga",
    "what is bitcoin price",
    "recommend a good movie",
    "",
])
def test_out_of_scope_questions_are_refused(q):
    assert gemini.is_out_of_domain_question(q, COLS) is True


def test_typo_correction_maps_to_columns():
    assert gemini.correct_question_typos("totl sales by regon", COLS) == "total sales by region"
    # words already correct, short words and numbers are left alone
    assert gemini.correct_question_typos("top 5 in Lahore", COLS) == "top 5 in Lahore"


def test_pure_record_count_is_a_count(monkeypatch):
    sql, params = gemini._early_patterns("how many records are there?", "t", "[t]", COLS)
    assert sql == "SELECT COUNT(*) AS [Total_Records] FROM [t];" and params == ()


def test_missing_measure_is_reported_not_substituted(monkeypatch):
    monkeypatch.setattr(gemini, "_numeric_and_date_columns", lambda t, c: (["Quantity", "UnitPrice", "Sales", "Profit"], ["OrderDate"]))
    sql, _ = gemini._early_patterns("total shipping cost by region", "t", "[t]", COLS)
    assert sql.startswith(gemini.DATA_NOT_FOUND_PREFIX)
    assert "shipping cost" in sql


def test_monthly_trend_groups_by_month(monkeypatch):
    monkeypatch.setattr(gemini, "_numeric_and_date_columns", lambda t, c: (["Quantity", "UnitPrice", "Sales", "Profit"], ["OrderDate"]))
    sql, _ = gemini._early_patterns("monthly sales trend", "t", "[t]", COLS)
    assert "GROUP BY FORMAT(TRY_CAST([OrderDate] AS DATE), 'yyyy-MM')" in sql
    assert "SUM(" in sql and "[Sales]" in sql


def test_no_sql_never_falls_back_to_select_star(monkeypatch):
    monkeypatch.setattr(gemini, "fast_pattern_sql_generator", lambda *a, **k: None)
    monkeypatch.setattr(gemini, "ask_gemini", lambda *a, **k: ("", None))
    monkeypatch.setattr(gemini, "system_cache", None)
    monkeypatch.setattr(gemini, "extract_sample_data_text", lambda t: "")
    res = gemini.execute_sql_with_retry("show sales for zone alpha", "t", COLS)
    assert res["success"] is False
    assert res.get("is_data_not_found") is True
    assert res["sql"] is None


def test_llm_unrelated_verdict_is_a_refusal(monkeypatch):
    monkeypatch.setattr(gemini, "fast_pattern_sql_generator", lambda *a, **k: None)
    monkeypatch.setattr(gemini, "ask_gemini", lambda *a, **k: (gemini.UNRELATED_MESSAGE, "m"))
    monkeypatch.setattr(gemini, "system_cache", None)
    monkeypatch.setattr(gemini, "extract_sample_data_text", lambda t: "")
    sql, params, fast, model = gemini.generate_sql("show sales for zone alpha", "t", COLS)
    assert sql == gemini.UNRELATED_MESSAGE
