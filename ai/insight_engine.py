"""
Automated Insights & Anomaly Detection Engine
4-Level Intelligence Pipeline:
- Level 1: Deterministic Summary & Key Aggregations
- Level 2: Trend Analysis & Multi-Category Performance
- Level 3: Statistical Anomaly & Spike/Drop Detection (Z-Score, IQR)
- Level 4: Grounded Executive AI Summary & Strategic Action Plan

Proactive Insights (period-over-period change + segment attribution) live at the bottom
of this file - see detect_period_over_period_change().
"""

import pandas as pd
from database.connection import run_query, sanitize_identifier


def detect_dataset_anomalies(df: pd.DataFrame) -> list:
    """
    Level 3: Statistical Anomaly Detection using Z-Score, IQR, and Rolling Deviation.
    Returns list of structured anomaly objects.
    """
    anomalies = []
    if df is None or df.empty:
        return anomalies

    cols = [c for c in df.columns if c.lower() != "recordid"]
    numeric_cols = df[cols].select_dtypes(include="number").columns.tolist()

    for col in numeric_cols:
        series = df[col].dropna()
        if len(series) < 8:
            continue

        # 1. Z-Score Anomaly Detection
        mean = series.mean()
        std = series.std()
        if std > 0:
            z_scores = ((series - mean) / std).abs()
            spike_mask = z_scores > 3.0
            spike_count = spike_mask.sum()
            if spike_count > 0:
                max_val = series[spike_mask].max()
                anomalies.append({
                    "column": col,
                    "type": "Unusual Spike",
                    "severity": "High" if spike_count >= 5 else "Medium",
                    "count": int(spike_count),
                    "max_anomaly_value": float(max_val),
                    "description": f"Column '{col}' exhibits {spike_count} statistical spikes exceeding 3 standard deviations (Peak: {max_val:,.2f})."
                })

        # 2. IQR Anomaly Detection
        q25, q75 = series.quantile(0.25), series.quantile(0.75)
        iqr = q75 - q25
        if iqr > 0:
            lower_bound = q25 - (1.5 * iqr)
            upper_bound = q75 + (1.5 * iqr)
            outliers = series[(series < lower_bound) | (series > upper_bound)]
            if len(outliers) > 0 and len(outliers) != spike_count:
                anomalies.append({
                    "column": col,
                    "type": "Distribution Outlier",
                    "severity": "Medium",
                    "count": len(outliers),
                    "max_anomaly_value": float(outliers.max()),
                    "description": f"Column '{col}' has {len(outliers)} distribution outliers beyond 1.5x Interquartile Range (IQR)."
                })

    return anomalies


def generate_automated_insights(table_name: str) -> dict:
    """
    Generate comprehensive automated business insights and anomalies for active dataset.
    """
    safe_tbl = sanitize_identifier(table_name)
    # See the identical comment in ai/data_quality.py::analyze_dataset_quality - an
    # unbounded SELECT * against a large dataset can take minutes before insight
    # generation even starts. 100k rows is still a statistically solid sample.
    df = run_query(f"SELECT TOP 100000 * FROM {safe_tbl};")

    if df is None or df.empty:
        return {
            "insights_level1": [],
            "insights_level2": [],
            "anomalies": [],
            "executive_summary": {}
        }

    cols = [c for c in df.columns if c.lower() != "recordid"]
    df_clean = df[cols]
    total_records = len(df_clean)

    insights_l1 = []
    insights_l2 = []

    numeric_cols = df_clean.select_dtypes(include="number").columns.tolist()
    text_cols = df_clean.select_dtypes(exclude="number").columns.tolist()

    # Level 1: Deterministic Metrics
    for col in text_cols:
        val_counts = df_clean[col].dropna().value_counts()
        if not val_counts.empty and val_counts.nunique() > 1:
            top_cat = val_counts.index[0]
            top_cnt = val_counts.iloc[0]
            top_pct = round((top_cnt / total_records) * 100, 1)
            insights_l1.append(f"Leading category in **{col}** is **{top_cat}** with {top_cnt:,} records ({top_pct}% of total volume).")

            if len(val_counts) > 1:
                bottom_cat = val_counts.index[-1]
                bottom_cnt = val_counts.iloc[-1]
                insights_l2.append(f"Lowest category in **{col}** is **{bottom_cat}** ({bottom_cnt:,} records).")

    for col in numeric_cols:
        col_sum = df_clean[col].sum()
        col_mean = df_clean[col].mean()
        insights_l1.append(f"Total aggregate **{col}** is **{col_sum:,.2f}** (Average: **{col_mean:,.2f}** per record).")

    # Level 3: Anomalies
    anomalies = detect_dataset_anomalies(df_clean)

    # Level 4: Grounded Executive AI Summary
    executive_summary = {
        "overview": f"Dataset contains {total_records:,} total records across {len(cols)} columns.",
        "key_findings": insights_l1[:3] if insights_l1 else ["Data demonstrates stable baseline distribution."],
        "major_trends": insights_l2[:3] if insights_l2 else ["Multi-category metrics show consistent performance."],
        "important_risks": [a["description"] for a in anomalies if a["severity"] == "High"] or ["No critical statistical risks detected."],
        "opportunities": [f"Optimize category '{text_cols[0]}' to capture additional volume."] if text_cols else ["Scale top-performing numerical metrics."],
        "recommended_actions": [
            "Review logistics & operational parameters for outlier records.",
            "Prioritize top-performing categories to maximize quarterly return."
        ]
    }

    return {
        "insights_level1": insights_l1,
        "insights_level2": insights_l2,
        "anomalies": anomalies,
        "executive_summary": executive_summary
    }


def detect_period_over_period_change(table_name: str, min_pct_change: float = 20.0) -> dict:
    """
    Deterministic week-over-week change detector for the "proactive insights" background
    scan: compares the most recent 7-day window of data present in the dataset against
    the 7-day window immediately before it, for the dataset's primary numeric metric, and
    (if a real categorical column exists) identifies which single segment contributed most
    to that change.

    No LLM involved here at all - this is pure pandas aggregation, same philosophy as the
    rest of the deterministic pipeline (Ask AI's fast pattern engine, the forecaster). The
    LLM is only used afterwards, by the caller, to phrase whatever this function finds in
    plain business language.

    Returns {"significant": False, "reason": "..."} when there's no usable date column, not
    enough historical span (needs 2 full weeks), or the change doesn't clear
    min_pct_change (default 20%) - callers should treat anything with significant=False as
    "nothing worth surfacing right now", not an error.
    """
    from ai.prediction import detect_time_and_target_columns

    try:
        safe_tbl = sanitize_identifier(table_name)
        df = run_query(f"SELECT TOP 200000 * FROM {safe_tbl};")
    except Exception as e:
        return {"significant": False, "reason": f"Could not read dataset: {e}"}

    if df is None or df.empty:
        return {"significant": False, "reason": "Dataset is empty."}

    date_col, metric_col = detect_time_and_target_columns(df)
    if not date_col or not metric_col:
        return {"significant": False, "reason": "No usable date/metric column pair found."}

    work = df[[date_col, metric_col]].copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work[metric_col] = pd.to_numeric(work[metric_col], errors="coerce")
    work = work.dropna(subset=[date_col, metric_col])
    if work.empty:
        return {"significant": False, "reason": "No valid date/metric values after cleaning."}

    latest_date = work[date_col].max()
    current_start = latest_date - pd.Timedelta(days=6)
    previous_start = current_start - pd.Timedelta(days=7)
    previous_end = current_start - pd.Timedelta(days=1)

    if work[date_col].min() > previous_start:
        return {"significant": False, "reason": "Fewer than 2 full weeks of historical data - not enough to compare periods yet."}

    current_mask = (work[date_col] >= current_start) & (work[date_col] <= latest_date)
    previous_mask = (work[date_col] >= previous_start) & (work[date_col] <= previous_end)

    current_total = float(work.loc[current_mask, metric_col].sum())
    previous_total = float(work.loc[previous_mask, metric_col].sum())

    if previous_total == 0:
        return {"significant": False, "reason": "Previous period total is zero - percentage change is undefined."}

    pct_change = round(((current_total - previous_total) / abs(previous_total)) * 100, 1)
    if abs(pct_change) < min_pct_change:
        return {"significant": False, "reason": f"Change of {pct_change}% is within normal range (threshold {min_pct_change}%)."}

    # Segment attribution: which categorical column's values moved the most, in the same
    # direction as the overall change, between the two periods.
    top_segment = None
    top_segment_col = None
    top_segment_contribution_pct = None
    try:
        from ai.schema_intelligence import get_dataset_schema_intelligence
        from ai.metric_resolver import get_real_categorical_columns
        schema_intel = get_dataset_schema_intelligence(table_name)
        cat_cols = get_real_categorical_columns(schema_intel)
        if cat_cols:
            seg_col = cat_cols[0]
            seg_df = df[[date_col, metric_col, seg_col]].copy()
            seg_df[date_col] = pd.to_datetime(seg_df[date_col], errors="coerce")
            seg_df[metric_col] = pd.to_numeric(seg_df[metric_col], errors="coerce")
            seg_df = seg_df.dropna(subset=[date_col, metric_col, seg_col])

            cur_by_seg = seg_df.loc[(seg_df[date_col] >= current_start) & (seg_df[date_col] <= latest_date)].groupby(seg_col)[metric_col].sum()
            prev_by_seg = seg_df.loc[(seg_df[date_col] >= previous_start) & (seg_df[date_col] <= previous_end)].groupby(seg_col)[metric_col].sum()
            delta_by_seg = (cur_by_seg.reindex(cur_by_seg.index.union(prev_by_seg.index), fill_value=0)
                            - prev_by_seg.reindex(cur_by_seg.index.union(prev_by_seg.index), fill_value=0))
            overall_delta = current_total - previous_total
            if not delta_by_seg.empty and overall_delta != 0:
                # The segment whose own change points the same direction as the overall
                # change and accounts for the largest share of it.
                same_direction = delta_by_seg[(delta_by_seg * overall_delta) > 0]
                if not same_direction.empty:
                    top_segment = same_direction.abs().idxmax()
                    top_segment_col = seg_col
                    top_segment_contribution_pct = round((same_direction[top_segment] / overall_delta) * 100, 1)
    except Exception:
        pass

    return {
        "significant": True,
        "table_name": table_name,
        "metric_col": metric_col,
        "date_col": date_col,
        "current_period": f"{current_start.strftime('%b %d')}-{latest_date.strftime('%b %d, %Y')}",
        "previous_period": f"{previous_start.strftime('%b %d')}-{previous_end.strftime('%b %d, %Y')}",
        "current_total": round(current_total, 2),
        "previous_total": round(previous_total, 2),
        "pct_change": pct_change,
        "direction": "increase" if pct_change > 0 else "decrease",
        "top_segment_col": top_segment_col,
        "top_segment": top_segment,
        "top_segment_contribution_pct": top_segment_contribution_pct,
    }


def phrase_proactive_insight(finding: dict) -> str:
    """
    Turns a detect_period_over_period_change() finding into a short, plain-business-
    language sentence for a notification. Tries the LLM first (for a more natural
    phrasing); always has a fully deterministic template fallback, so a slow/unavailable
    LLM never blocks a real, already-detected finding from being surfaced.
    """
    if not finding.get("significant"):
        return ""

    metric = finding["metric_col"]
    pct = abs(finding["pct_change"])
    direction = finding["direction"]
    cur_period = finding["current_period"]
    prev_period = finding["previous_period"]

    segment_clause = ""
    if finding.get("top_segment") and finding.get("top_segment_contribution_pct") is not None:
        segment_clause = f" {finding['top_segment_col']} '{finding['top_segment']}' accounts for roughly {abs(finding['top_segment_contribution_pct'])}% of that change."

    fallback_text = (
        f"{metric} {direction}d by {pct}% ({cur_period} vs {prev_period}: "
        f"{finding['current_total']:,.0f} vs {finding['previous_total']:,.0f})."
        f"{segment_clause}"
    )

    try:
        from ai.gemini import ask_gemini
        prompt = (
            f"Write ONE short, plain-English business sentence (max 30 words, no markdown) "
            f"reporting this data finding: {metric} {direction}d {pct}% comparing {cur_period} "
            f"to {prev_period} ({finding['previous_total']:,.0f} -> {finding['current_total']:,.0f})."
            + (f" The main driver was {finding['top_segment_col']} '{finding['top_segment']}' "
               f"({abs(finding['top_segment_contribution_pct'])}% of the change)." if finding.get("top_segment") else "")
        )
        # ask_gemini's own `timeout` argument is passed straight through to the OpenAI SDK
        # call per-model, but that alone turned out NOT to be a reliable ceiling in
        # practice - a slow/unresponsive OpenRouter endpoint made a single call block for
        # 30+ seconds in testing, well past its stated 2.0s. This function runs inside the
        # shared background scheduler thread (utils.scheduler._scheduler_loop), which also
        # drives alert rules, scheduled reports, and the subscription-expiry sweep - an
        # LLM call that doesn't actually return in bounded time would stall ALL of those,
        # not just this one dataset's insight, for as long as the hang lasts.
        #
        # A ThreadPoolExecutor used as `with ... as ex:` would not actually fix this: its
        # __exit__ calls shutdown(wait=True), which blocks the calling thread until the
        # hung worker finishes anyway, timeout or not. Using a plain daemon Thread + Queue
        # instead means get(timeout=...) really does return on time, and the abandoned
        # worker thread (daemon=True) can never block process/thread shutdown later.
        import threading
        import queue as _queue
        result_q = _queue.Queue(maxsize=1)

        def _ask_worker():
            try:
                result_q.put(ask_gemini(prompt, 80, 2.0))
            except Exception:
                result_q.put((None, None))

        threading.Thread(target=_ask_worker, daemon=True).start()
        try:
            text, _model = result_q.get(timeout=4.0)
            text = (text or "").strip().strip('"')
            if text and 10 < len(text) < 300:
                return text
        except _queue.Empty:
            pass  # LLM call didn't return in time; deterministic fallback below is used instead
    except Exception:
        pass

    return fallback_text
