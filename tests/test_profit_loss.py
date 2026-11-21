"""R-Phase 9.4: deterministic profit/loss analysis on a profit, a loss and an unsuitable dataset."""
import numpy as np
import pandas as pd

from ai.dataset_brief import summarize
from ai.profit_loss import DISCLAIMER, analyze_profit_loss


def _months(n, start="2024-01-01"):
    return pd.date_range(start, periods=n, freq="MS")


def profit_df():
    rng = np.random.default_rng(1)
    rows = []
    for i, m in enumerate(_months(26)):
        for region, base in (("North", 1200), ("South", 800), ("East", 500)):
            rev = base * (1 + 0.03 * i) * (1.2 if m.month == 12 else 1.0) + rng.normal(0, 20)
            rows.append({"Date": m.strftime("%Y-%m-%d"), "Region": region, "Revenue": round(rev, 2), "Cost": round(rev * 0.7, 2)})
    return pd.DataFrame(rows)


def loss_df():
    rows = []
    for i, m in enumerate(_months(12)):
        for store, rev, cost in (("Mall", 1000, 1300 + 15 * i), ("Market", 600, 640 + 10 * i)):
            rows.append({"Month": m.strftime("%Y-%m"), "Store": store, "Sales": rev, "Expenses": cost})
    return pd.DataFrame(rows)


def unsuitable_df():
    return pd.DataFrame({"Name": ["Ali", "Sara", "Omar", "Hina"], "Department": ["HR", "IT", "IT", "Ops"], "Age": [31, 28, 45, 39]})


def test_profit_dataset():
    df = profit_df()
    r = analyze_profit_loss(df)
    assert r["suitable"] and r["status"] == "profit"
    exp_profit = round(float((df.Revenue - df.Cost).sum()), 2)
    assert abs(r["total_profit"] - exp_profit) < 0.05
    assert abs(r["margin_pct"] - exp_profit / df.Revenue.sum() * 100) < 0.01
    assert r["trend"]["direction"] == "rising" and r["trend"]["months"] == 26
    f = r["forecast"]
    assert len(f["months"]) == 3 and f["months"][0] == "2026-03"
    assert "seasonal" in f["method"]            # 26 months -> month-of-year adjustment
    assert all(lo <= v <= hi for lo, v, hi in zip(f["low"], f["values"], f["high"]))
    assert r["drivers"][0]["column"] == "Region" and r["drivers"][0]["top"][0][0] == "North"
    assert r["disclaimer"] == DISCLAIMER and DISCLAIMER in r["explanation"]


def test_loss_dataset():
    df = loss_df()
    r = analyze_profit_loss(df)
    assert r["suitable"] and r["status"] == "loss"
    assert r["columns"]["revenue"] == "Sales" and r["columns"]["cost"] == "Expenses"
    assert abs(r["total_profit"] - float((df.Sales - df.Expenses).sum())) < 0.01
    assert r["margin_pct"] < 0
    assert r["trend"]["direction"] == "falling" and r["trend"]["loss_months"] == 12
    assert r["forecast"]["confidence"] in ("low", "medium") and "seasonality" in r["forecast"]["confidence_note"].lower()
    assert "loss-making" in r["explanation"] and "Mall" in r["explanation"]
    assert r["top_costs"]["items"][0][0] == "Mall"


def test_unsuitable_dataset_says_what_is_missing():
    r = analyze_profit_loss(unsuitable_df())
    assert r["suitable"] is False
    assert "revenue/sales" in r["message"] and "cost/expense" in r["message"]
    assert "status" not in r and r["disclaimer"] == DISCLAIMER


def test_summary_is_deterministic_and_exact(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    df = loss_df()
    out = summarize(df)
    assert out["source"] == "computed"
    f = out["facts"]
    assert f["rows"] == 24 and f["totals"]["Sales"] == float(df.Sales.sum())
    assert f"{int(df.Sales.sum()):,}" in out["summary"]
    assert "no duplicate rows" in out["summary"]


def test_summary_falls_back_when_llm_fails(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    import ai.gemini as g
    monkeypatch.setattr(g, "ask_gemini", lambda *a, **k: (None, None))
    out = summarize(profit_df())
    assert out["source"] == "computed" and out["summary"].startswith("78 rows")
