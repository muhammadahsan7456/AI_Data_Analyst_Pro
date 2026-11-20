"""
Short plain-language dataset summary (R-Phase 9.4).

Facts are computed deterministically (size, key columns, exact totals, top category,
date range, data quality). The text is built from those facts; an LLM may only re-phrase it
(ai.profit_loss.phrase_with_llm, hard timeout, numbers checked) and the computed text is
always the fallback.
"""
import pandas as pd

from ai.profit_loss import detect_columns, phrase_with_llm


def _fmt(v):
    v = float(v)
    return f"{int(v):,}" if v.is_integer() else f"{v:,.2f}"


def compute_facts(df: pd.DataFrame) -> dict:
    cols = detect_columns(df)
    numeric = [c for c in df.columns
               if pd.to_numeric(df[c], errors="coerce").notna().mean() >= 0.9 and not str(c).lower().endswith("id")
               and str(c).lower() != "recordid"]
    measures = [c for c in (cols["revenue"], cols["profit"], cols["cost"]) if c] or numeric[:2]
    totals = {c: round(float(pd.to_numeric(df[c], errors="coerce").sum()), 2) for c in measures[:3]}
    facts = {
        "rows": int(len(df)),
        "columns": int(len([c for c in df.columns if str(c).lower() != "recordid"])),
        "numeric_columns": numeric[:6],
        "category_columns": cols["dims"][:4],
        "date_column": cols["date"],
        "totals": totals,
        "missing_cells": int(df.isna().sum().sum()),
        "missing_pct": round(float(df.isna().mean().mean() * 100), 2) if len(df) else 0.0,
        "duplicate_rows": int(df.drop(columns=[c for c in df.columns if str(c).lower() == "recordid"]).duplicated().sum()),
    }
    if cols["date"]:
        d = pd.to_datetime(df[cols["date"]].astype(str), errors="coerce").dropna()
        if len(d):
            facts["date_range"] = (d.min().strftime("%Y-%m-%d"), d.max().strftime("%Y-%m-%d"))
    if cols["dims"] and measures:
        dim, mea = cols["dims"][0], measures[0]
        g = pd.to_numeric(df[mea], errors="coerce").groupby(df[dim].astype(str)).sum().sort_values(ascending=False)
        if len(g):
            share = (g.iloc[0] / g.sum() * 100) if g.sum() else 0
            facts["top_category"] = {"column": dim, "measure": mea, "name": str(g.index[0]),
                                     "value": round(float(g.iloc[0]), 2), "share_pct": round(float(share), 1)}
    return facts


def build_text(f: dict) -> str:
    parts = [f"{f['rows']:,} rows and {f['columns']} columns."]
    keys = f["category_columns"][:3] + [c for c in f["numeric_columns"][:3]]
    if keys:
        parts.append("Key columns: " + ", ".join(keys) + ".")
    if f.get("date_range"):
        parts.append(f"Dates from {f['date_range'][0]} to {f['date_range'][1]} ({f['date_column']}).")
    if f["totals"]:
        parts.append("Totals: " + ", ".join(f"{k} {_fmt(v)}" for k, v in f["totals"].items()) + ".")
    tc = f.get("top_category")
    if tc:
        parts.append(f"Largest {tc['column']} by {tc['measure']}: {tc['name']} ({_fmt(tc['value'])}, {tc['share_pct']}% of the total).")
    q = []
    q.append(f"{f['missing_cells']:,} empty cells ({f['missing_pct']}%)" if f["missing_cells"] else "no empty cells")
    q.append(f"{f['duplicate_rows']:,} duplicate rows" if f["duplicate_rows"] else "no duplicate rows")
    parts.append("Data quality: " + ", ".join(q) + ".")
    return " ".join(parts)


def summarize(df: pd.DataFrame, use_llm: bool = True) -> dict:
    facts = compute_facts(df)
    text = build_text(facts)
    source = "computed"
    if use_llm:
        text, source = phrase_with_llm(text, timeout=4.0)
    return {"summary": text, "facts": facts, "source": source}
