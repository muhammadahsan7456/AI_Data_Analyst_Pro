"""
Deterministic profit / loss analysis (R-Phase 9.4).

Everything here is computed from the dataset with pandas/numpy: whether the business is in
profit or loss, the margin, the monthly trend, a short forecast with a confidence note and
the reasons (top contributing categories, best/worst months, biggest costs). An LLM may only
re-phrase the finished explanation (see phrase_with_llm); the numbers never come from it.

If the dataset lacks usable columns, the result says exactly what is missing instead of
guessing. Every result carries DISCLAIMER.
"""
import re

import numpy as np
import pandas as pd

DISCLAIMER = "Analysis estimate from your data, not financial advice."

_REVENUE = ("revenue", "sales", "turnover", "income", "amount", "totalsales", "grosssales", "netsales")
_COST = ("cost", "costs", "expense", "expenses", "cogs", "spend", "expenditure", "purchase")
_PROFIT = ("profit", "netprofit", "grossprofit", "earnings", "netincome", "pnl", "profitloss")
_DATE = ("date", "month", "period", "day", "time", "orderdate", "invoicedate", "createdat")


def _norm(c):
    return re.sub(r"[^a-z]", "", str(c).lower())


def _pick(columns, names, numeric_cols):
    """First numeric column whose normalised name equals, then contains, a candidate."""
    cols = [c for c in columns if c in numeric_cols]
    for want in names:
        for c in cols:
            if _norm(c) == want:
                return c
    for want in names:
        for c in cols:
            if want in _norm(c):
                return c
    return None


def detect_columns(df: pd.DataFrame) -> dict:
    numeric = set()
    for c in df.columns:
        s = pd.to_numeric(df[c], errors="coerce")
        if len(s) and s.notna().mean() >= 0.9 and not _norm(c).endswith("id"):
            numeric.add(c)
    profit = _pick(df.columns, _PROFIT, numeric)
    revenue = _pick([c for c in df.columns if c != profit], _REVENUE, numeric)
    cost = _pick([c for c in df.columns if c not in (profit, revenue)], _COST, numeric)
    date = None
    for c in df.columns:
        if c in numeric and not any(k in _norm(c) for k in ("year", "month")):
            continue
        if any(k in _norm(c) for k in _DATE):
            parsed = pd.to_datetime(df[c].astype(str), errors="coerce")
            if parsed.notna().mean() >= 0.8:
                date = c
                break
    dims = []
    for c in df.columns:
        if c in numeric or c == date:
            continue
        n = df[c].nunique(dropna=True)
        if 2 <= n <= 50:
            dims.append((n, c))
    dims = [c for _, c in sorted(dims)]
    return {"profit": profit, "revenue": revenue, "cost": cost, "date": date, "dims": dims}


def _fmt(v):
    return f"{v:,.2f}"


def _confidence(n_months, r2, cv):
    if n_months < 6:
        return "low", f"only {n_months} months of history"
    if n_months >= 18 and r2 >= 0.5 and cv < 0.35:
        return "high", f"{n_months} months of history and a steady trend"
    if n_months >= 9 and (r2 >= 0.25 or cv < 0.5):
        return "medium", f"{n_months} months of history; month-to-month variation is moderate"
    return "low", f"{n_months} months of history with large month-to-month swings"


def analyze_profit_loss(df: pd.DataFrame, horizon_months: int = 3) -> dict:
    if df is None or df.empty:
        return {"suitable": False, "missing": ["data rows"], "message": "This dataset has no rows to analyse.", "disclaimer": DISCLAIMER}
    cols = detect_columns(df)
    profit_col, rev_col, cost_col, date_col = cols["profit"], cols["revenue"], cols["cost"], cols["date"]

    work = pd.DataFrame(index=df.index)
    if rev_col:
        work["revenue"] = pd.to_numeric(df[rev_col], errors="coerce").fillna(0.0)
    if cost_col:
        work["cost"] = pd.to_numeric(df[cost_col], errors="coerce").fillna(0.0)
    if profit_col:
        work["profit"] = pd.to_numeric(df[profit_col], errors="coerce").fillna(0.0)
        basis = f"the {profit_col} column"
    elif rev_col and cost_col:
        work["profit"] = work["revenue"] - work["cost"]
        basis = f"{rev_col} minus {cost_col}"
    else:
        missing = []
        if not rev_col:
            missing.append("a revenue/sales column")
        if not cost_col:
            missing.append("a cost/expense column")
        missing_txt = " and ".join(missing) if missing else "a cost column"
        return {
            "suitable": False,
            "missing": missing + ["or a profit column"],
            "found": {k: v for k, v in cols.items() if k != "dims" and v},
            "message": (f"I can't tell whether this business is in profit or loss: the dataset needs a profit column, "
                        f"or both a revenue/sales column and a cost/expense column. Missing: {missing_txt}."),
            "disclaimer": DISCLAIMER,
        }

    total_profit = float(work["profit"].sum())
    total_rev = float(work["revenue"].sum()) if rev_col else None
    total_cost = float(work["cost"].sum()) if cost_col else (total_rev - total_profit if total_rev is not None else None)
    margin = (total_profit / total_rev * 100.0) if total_rev else None
    tol = max(1.0, abs(total_rev or 0) * 0.001)
    status = "profit" if total_profit > tol else ("loss" if total_profit < -tol else "break-even")

    res = {
        "suitable": True,
        "status": status,
        "basis": basis,
        "columns": {"profit": profit_col, "revenue": rev_col, "cost": cost_col, "date": date_col},
        "rows": int(len(df)),
        "total_profit": round(total_profit, 2),
        "total_revenue": round(total_rev, 2) if total_rev is not None else None,
        "total_cost": round(total_cost, 2) if total_cost is not None else None,
        "margin_pct": round(margin, 2) if margin is not None else None,
        "disclaimer": DISCLAIMER,
        "drivers": [],
        "trend": None,
        "forecast": None,
    }

    # Why: contributions by the most useful category column.
    for dim in cols["dims"][:3]:
        g = work.assign(_d=df[dim].astype(str)).groupby("_d")["profit"].sum().sort_values(ascending=False)
        if len(g) < 2:
            continue
        top = [(k, round(float(v), 2)) for k, v in g.head(3).items()]
        bottom = [(k, round(float(v), 2)) for k, v in g.tail(3).items() if v < 0 or len(g) > 3]
        res["drivers"].append({"column": dim, "top": top, "bottom": bottom[::-1]})
        if len(res["drivers"]) >= 2:
            break
    if cost_col and cols["dims"]:
        dim = cols["dims"][0]
        cg = work.assign(_d=df[dim].astype(str)).groupby("_d")["cost"].sum().sort_values(ascending=False)
        res["top_costs"] = {"column": dim, "items": [(k, round(float(v), 2)) for k, v in cg.head(3).items()]}

    # Trend + forecast by month.
    if date_col:
        d = pd.to_datetime(df[date_col].astype(str), errors="coerce")
        m = work.assign(_m=d.dt.to_period("M")).dropna(subset=["_m"]).groupby("_m")["profit"].sum().sort_index()
        if len(m) >= 2:
            full = pd.period_range(m.index.min(), m.index.max(), freq="M")
            m = m.reindex(full, fill_value=0.0)
            y = m.values.astype(float)
            x = np.arange(len(y), dtype=float)
            slope, intercept = np.polyfit(x, y, 1) if len(y) >= 2 else (0.0, float(y[0]))
            fitted = slope * x + intercept
            ss_res = float(((y - fitted) ** 2).sum())
            ss_tot = float(((y - y.mean()) ** 2).sum()) or 1.0
            r2 = max(0.0, 1 - ss_res / ss_tot)
            mean_abs = float(np.abs(y).mean()) or 1.0
            cv = float(np.std(y - fitted) / mean_abs)
            rel = slope / mean_abs
            direction = "rising" if rel > 0.02 else ("falling" if rel < -0.02 else "flat")
            best = m.idxmax(); worst = m.idxmin()
            res["trend"] = {
                "months": int(len(m)), "direction": direction, "slope_per_month": round(float(slope), 2),
                "series": [(str(k), round(float(v), 2)) for k, v in m.items()],
                "best_month": (str(best), round(float(m[best]), 2)),
                "worst_month": (str(worst), round(float(m[worst]), 2)),
                "loss_months": int((m < 0).sum()),
            }
            # Seasonality: month-of-year factor only with >= 24 months of history.
            seasonal = None
            if len(m) >= 24:
                resid = pd.Series(y - fitted, index=[p.month for p in m.index])
                seasonal = resid.groupby(level=0).mean().to_dict()
            fut = pd.period_range(m.index.max() + 1, periods=horizon_months, freq="M")
            fx = np.arange(len(y), len(y) + horizon_months, dtype=float)
            point = slope * fx + intercept
            if seasonal:
                point = point + np.array([seasonal.get(p.month, 0.0) for p in fut])
            spread = float(np.std(y - fitted)) * 1.28  # ~80% band
            level, why = _confidence(len(m), r2, cv)
            res["forecast"] = {
                "months": [str(p) for p in fut],
                "values": [round(float(v), 2) for v in point],
                "low": [round(float(v - spread), 2) for v in point],
                "high": [round(float(v + spread), 2) for v in point],
                "confidence": level,
                "confidence_note": f"{level.capitalize()} confidence: {why}." + (" Month-of-year seasonality included." if seasonal else " Not enough history (24+ months) for seasonality."),
                "method": "linear trend on monthly totals" + (" + month-of-year seasonal adjustment" if seasonal else ""),
                "next_total": round(float(point.sum()), 2),
            }
    res["explanation"] = build_explanation(res)
    return res


def build_explanation(r: dict) -> str:
    if not r.get("suitable"):
        return r.get("message", "")
    parts = []
    word = {"profit": "in profit", "loss": "at a loss", "break-even": "roughly at break-even"}[r["status"]]
    line = f"Based on {r['rows']:,} rows ({r['basis']}), the business is {word}: total profit {_fmt(r['total_profit'])}"
    if r.get("total_revenue") is not None:
        line += f" on revenue of {_fmt(r['total_revenue'])}"
    if r.get("margin_pct") is not None:
        line += f", a margin of {r['margin_pct']:.2f}%"
    parts.append(line + ".")
    t = r.get("trend")
    if t:
        parts.append(f"Over {t['months']} months the monthly profit trend is {t['direction']} "
                     f"({_fmt(t['slope_per_month'])} per month). Best month {t['best_month'][0]} ({_fmt(t['best_month'][1])}), "
                     f"weakest {t['worst_month'][0]} ({_fmt(t['worst_month'][1])})"
                     + (f"; {t['loss_months']} month(s) made a loss." if t["loss_months"] else "."))
    for d in r.get("drivers", [])[:2]:
        tops = ", ".join(f"{k} ({_fmt(v)})" for k, v in d["top"])
        line = f"By {d['column']}, the largest contributors are {tops}"
        neg = [(k, v) for k, v in d.get("bottom", []) if v < 0]
        if neg:
            line += "; loss-making: " + ", ".join(f"{k} ({_fmt(v)})" for k, v in neg)
        parts.append(line + ".")
    if r.get("top_costs"):
        tc = r["top_costs"]
        parts.append(f"Biggest costs by {tc['column']}: " + ", ".join(f"{k} ({_fmt(v)})" for k, v in tc["items"]) + ".")
    f = r.get("forecast")
    if f:
        parts.append(f"Forecast for the next {len(f['months'])} months ({f['months'][0]} to {f['months'][-1]}): about "
                     f"{_fmt(f['next_total'])} in total profit. {f['confidence_note']}")
    parts.append(r["disclaimer"])
    return " ".join(parts)


def phrase_with_llm(text: str, timeout: float = 4.0) -> tuple:
    """Optionally let the LLM re-phrase a finished, fact-checked explanation. Returns
    (text, source) where source is "ai" or "computed". Any failure or timeout returns the
    deterministic text unchanged."""
    import os
    if not os.getenv("OPENROUTER_API_KEY"):
        return text, "computed"
    try:
        from ai.gemini import ask_gemini
        prompt = ("Rewrite this business analysis in clear, plain English for a small-business owner. "
                  "Keep EVERY number exactly as written, add no new facts, keep the last sentence.\n\n" + text)
        out, model = ask_gemini(prompt, max_tokens=400, timeout=timeout)
        out = (out or "").strip()
        nums = re.findall(r"-?[\d,]+\.\d+", text)
        if out and model and all(n in out for n in nums):
            return out, "ai"
    except Exception:
        pass
    return text, "computed"
