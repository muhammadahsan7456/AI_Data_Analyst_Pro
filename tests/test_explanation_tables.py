"""Markdown tables in Ask AI explanations render as HTML tables (separator rows with spaces too) and stay escaped."""
from ai.data_summary import format_ai_explanation


def test_spaced_separator_table_renders_and_keeps_lead_sentence():
    text = "### Direct Answer\nThe **Total Orders** is **600**.\n| Metric Name | Value |\n| :--- | :--- |\n| **Total Orders** | **600** |"
    out = format_ai_explanation(text)
    assert "<table" in out and "<th" in out
    assert "Metric Name" in out and "<strong>600</strong>" in out
    assert "| :---" not in out and "\\1" not in out
    assert "is <strong>600</strong>." in out


def test_table_cells_are_escaped():
    out = format_ai_explanation("|a|b|\n|---|---|\n|1|<img src=x onerror=alert(1)>|")
    assert "<img" not in out and "&lt;img" in out


def test_chat_template_has_single_value_kpi_path():
    src = open("templates/chat.html", encoding="utf-8").read()
    assert "function isSingleValueSpec" in src and "renderSingleValueCard(spec, _target)" in src
    assert 'layout.yaxis.type = "category"' in src  # funnel labels stay categories
