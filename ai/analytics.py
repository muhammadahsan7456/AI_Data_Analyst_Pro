import numpy as np
import pandas as pd


def get_column_profiling(df: pd.DataFrame) -> list:
    """
    Generate detailed column-by-column profiling report with comprehensive descriptive statistics.
    """
    profile = []
    total_len = len(df)

    for col in df.columns:
        series = df[col]
        dtype = str(series.dtype)
        missing = int(series.isnull().sum())
        missing_pct = round((missing / total_len) * 100, 2) if total_len > 0 else 0.0
        unique_vals = int(series.nunique())
        
        non_null = series.dropna()
        sample_vals = non_null.unique()[:3]
        sample_str = ", ".join(map(str, sample_vals)) if len(sample_vals) > 0 else "N/A"

        col_data = {
            "name": col,
            "dtype": dtype,
            "missing": missing,
            "missing_pct": missing_pct,
            "unique": unique_vals,
            "sample": sample_str,
            "is_numeric": pd.api.types.is_numeric_dtype(series),
            "stats": {}
        }

        # Descriptive Statistics for Numeric Columns
        if pd.api.types.is_numeric_dtype(series) and len(non_null) > 0:
            min_val = float(non_null.min())
            max_val = float(non_null.max())
            mean_val = float(non_null.mean())
            median_val = float(non_null.median())
            std_val = float(non_null.std()) if len(non_null) > 1 else 0.0
            zero_count = int((non_null == 0).sum())

            col_data["stats"] = {
                "min": round(min_val, 2),
                "max": round(max_val, 2),
                "mean": round(mean_val, 2),
                "median": round(median_val, 2),
                "std": round(std_val, 2),
                "zero_count": zero_count
            }

        # Descriptive Statistics for Categorical / Text Columns
        elif len(non_null) > 0:
            val_counts = non_null.value_counts()
            if not val_counts.empty:
                top_val = str(val_counts.index[0])
                top_freq = int(val_counts.iloc[0])
                top_pct = round((top_freq / total_len) * 100, 1) if total_len > 0 else 0.0
                col_data["stats"] = {
                    "top_value": top_val[:40],
                    "top_freq": top_freq,
                    "top_pct": top_pct
                }

        profile.append(col_data)

    return profile


def detect_outliers_iqr(df: pd.DataFrame) -> dict:
    """
    Detect outliers in numeric columns using Interquartile Range (IQR).
    """
    numeric_df = df.select_dtypes(include=[np.number])
    outliers_report = {}

    for col in numeric_df.columns:
        series = numeric_df[col].dropna()
        if len(series) < 4:
            continue
        q1 = series.quantile(0.25)
        q3 = series.quantile(0.75)
        iqr = q3 - q1
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr
        
        outlier_mask = (series < lower_bound) | (series > upper_bound)
        outlier_count = int(outlier_mask.sum())
        
        if outlier_count > 0:
            outliers_report[col] = {
                "count": outlier_count,
                "percentage": round((outlier_count / len(series)) * 100, 2),
                "lower_bound": round(float(lower_bound), 2),
                "upper_bound": round(float(upper_bound), 2)
            }

    return outliers_report


def get_correlation_matrix(df: pd.DataFrame) -> dict:
    """
    Compute correlation matrix for numeric features.
    """
    numeric_df = df.select_dtypes(include=[np.number])
    if numeric_df.shape[1] < 2:
        return {}
    
    corr = numeric_df.corr().round(2)
    return corr.to_dict()


def generate_data_quality_report(df: pd.DataFrame) -> dict:
    """
    Compute overall data quality summary and detailed statistics using data_health_engine.
    """
    from ai.data_health_engine import compute_dataset_health_score
    health_info = compute_dataset_health_score(df)

    total_missing = health_info.get("null_cells", int(df.isnull().sum().sum()))
    total_duplicates = health_info.get("duplicate_rows", int(df.duplicated().sum()))
    health_score = health_info.get("score", 100.0)

    return {
        "health_score": health_score,
        "health_rating": health_info.get("rating", "Good"),
        "total_rows": len(df),
        "total_cols": len(df.columns),
        "total_missing": total_missing,
        "total_duplicates": total_duplicates,
        "column_profiles": get_column_profiling(df),
        "outliers": detect_outliers_iqr(df),
        "breakdown": health_info.get("breakdown", {}),
        "alerts": health_info.get("alerts", [])
    }


def clean_dataset(df: pd.DataFrame, remove_duplicates: bool = True, fill_missing: str = "auto") -> pd.DataFrame:
    """
    Perform AI Data Cleaning pipeline.
    """
    cleaned_df = df.copy()

    if remove_duplicates:
        cleaned_df = cleaned_df.drop_duplicates()

    if fill_missing == "drop":
        cleaned_df = cleaned_df.dropna()
    elif fill_missing in ["auto", "mean"]:
        for col in cleaned_df.columns:
            if pd.api.types.is_numeric_dtype(cleaned_df[col]):
                mean_val = cleaned_df[col].mean()
                if not pd.isna(mean_val):
                    cleaned_df[col] = cleaned_df[col].fillna(mean_val)
            else:
                mode_val = cleaned_df[col].mode()
                if not mode_val.empty:
                    cleaned_df[col] = cleaned_df[col].fillna(mode_val[0])

    return cleaned_df


def generate_ai_insights(df: pd.DataFrame) -> list:
    """
    Generate rich executive business insights and detailed statistical findings.
    """
    if df is None or df.empty:
        return ["Dataset is empty or contains 0 matching records."]

    insights = []
    report = generate_data_quality_report(df)

    # 1. Health & Quality Insight
    if report["health_score"] >= 85:
        insights.append("✅ Data Quality Excellent: Dataset has an optimal health score with high completeness.")
    elif report["health_score"] >= 65:
        insights.append("⚠️ Data Quality Moderate: Moderate health score. Consider handling missing values or duplicates.")
    else:
        insights.append("❌ Data Quality Attention Required: High missingness or duplicate rows detected. AI Data Cleaning recommended.")

    # 2. Duplicate Rows Insight
    if report["total_duplicates"] > 0:
        insights.append(f"🔍 Duplication Alert: Found {report['total_duplicates']} exact duplicate rows ({round((report['total_duplicates']/len(df))*100, 1)}% of total).")

    # 3. Outlier Anomaly Detection Insight
    outlier_cols = list(report["outliers"].keys())
    if outlier_cols:
        details = []
        for col in outlier_cols[:3]:
            count = report["outliers"][col]["count"]
            pct = report["outliers"][col]["percentage"]
            details.append(f"[{col}] ({count} outliers, {pct}%)")
        insights.append(f"📊 Numeric Outliers Detected: Statistically significant anomalies found in: {', '.join(details)}.")

    # 4. Correlation & Feature Interdependence Insight
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    if len(numeric_cols) >= 2:
        corr = df[numeric_cols].corr()
        strong_pairs = []
        for i in range(len(numeric_cols)):
            for j in range(i + 1, len(numeric_cols)):
                val = corr.iloc[i, j]
                if abs(val) >= 0.65:
                    col1, col2 = numeric_cols[i], numeric_cols[j]
                    relationship = "Strong Positive" if val > 0 else "Strong Negative"
                    strong_pairs.append(f"{relationship} correlation ({round(val, 2)}) between [{col1}] and [{col2}]")
        if strong_pairs:
            insights.append(f"📈 Feature Relationships: {'; '.join(strong_pairs[:2])}.")

    # 5. Categorical Skewness / Dominance Insight
    for prof in report["column_profiles"]:
        if not prof["is_numeric"] and "stats" in prof and "top_pct" in prof["stats"]:
            if prof["stats"]["top_pct"] >= 65.0:
                insights.append(f"📌 Categorical Skew: Column [{prof['name']}] is heavily skewed towards '{prof['stats']['top_value']}' ({prof['stats']['top_pct']}% of records).")

    if not insights:
        insights.append("💡 Dataset is well-structured and balanced. Ready for advanced SQL analytics and visualization.")

    return insights


def generate_auto_kpi_dashboard(table_name: str, df: pd.DataFrame) -> dict:
    """
    Auto-generates a sensible default KPI/chart dashboard for a dataset with zero user
    input - meant to appear right on the dataset's profiling page as soon as it's
    uploaded, not just when a question is asked about it.

    Column roles (which column is the main numeric metric, which is the main category to
    break it down by, which is a date axis) are resolved via the SAME schema-intelligence
    + metric-resolver logic Ask AI's query-plan engine uses (ai.schema_intelligence,
    ai.metric_resolver) rather than a separate hand-rolled detector, so "what this column
    means" is answered identically everywhere in the app.

    Degrades through several levels depending on what the dataset actually has: a metric
    + a category + a date column gets the full 3-chart treatment; fewer real columns just
    means fewer KPI cards/charts, never an error - a dataset with no clear numeric column
    still gets a basic row-count/column-count/top-category summary.
    """
    from ai.schema_intelligence import get_dataset_schema_intelligence
    from ai.metric_resolver import get_real_numeric_columns, get_real_categorical_columns

    if df is None or df.empty:
        return {"success": False, "reason": "Dataset is empty."}

    try:
        schema_intel = get_dataset_schema_intelligence(table_name)
    except Exception:
        schema_intel = {}

    categorical_cols = [c for c in get_real_categorical_columns(schema_intel) if c in df.columns] if schema_intel else []
    date_cols = [c for c in schema_intel.get("date_columns", []) if c in df.columns] if schema_intel else []

    # schema_intelligence classifies column semantic type from a 300-row SAMPLE, so it can
    # occasionally call a column "numeric" that, over the full loaded dataframe, is mostly
    # non-numeric text (observed in testing: a free-text "Comment" column classified as
    # numeric) - using it as-is for SUM/AVG KPIs or a groupby().sum() chart would silently
    # produce NaN/string-concatenation nonsense instead of a real number. Re-validated here
    # against the actual df this function received, not trusted blindly.
    numeric_cols = []
    if schema_intel:
        for c in get_real_numeric_columns(schema_intel):
            if c not in df.columns:
                continue
            coerced = pd.to_numeric(df[c], errors="coerce")
            if coerced.notna().mean() >= 0.8:
                numeric_cols.append(c)

    total_rows = len(df)
    total_cols = len([c for c in df.columns if c.lower() != "recordid"])

    kpis = [{"label": "Total Records", "value": f"{total_rows:,}"}]

    primary_metric = numeric_cols[0] if numeric_cols else None
    secondary_metric = numeric_cols[1] if len(numeric_cols) > 1 else None
    primary_cat = categorical_cols[0] if categorical_cols else None

    if primary_metric:
        series = pd.to_numeric(df[primary_metric], errors="coerce").dropna()
        if not series.empty:
            kpis.append({"label": f"Total {primary_metric}", "value": f"{series.sum():,.2f}"})
            kpis.append({"label": f"Average {primary_metric}", "value": f"{series.mean():,.2f}"})
    if secondary_metric:
        series2 = pd.to_numeric(df[secondary_metric], errors="coerce").dropna()
        if not series2.empty:
            kpis.append({"label": f"Total {secondary_metric}", "value": f"{series2.sum():,.2f}"})
    if primary_cat:
        kpis.append({"label": f"Distinct {primary_cat}", "value": f"{df[primary_cat].nunique():,}"})
    if not primary_metric and not primary_cat:
        # Minimal fallback: no clear numeric metric or category at all (e.g. a dataset of
        # mostly IDs/free text) - still a genuinely useful, non-nonsensical summary.
        kpis.append({"label": "Total Columns", "value": f"{total_cols:,}"})

    kpis = kpis[:5]

    charts = []  # each: {"title": str, "chart_path": str}
    try:
        from visualization.chart_generator import generate_chart

        if primary_metric and primary_cat:
            grouped = (
                df.assign(**{primary_metric: pd.to_numeric(df[primary_metric], errors="coerce")})
                .groupby(primary_cat, dropna=True)[primary_metric]
                .sum()
                .sort_values(ascending=False)
                .head(10)
                .reset_index()
            )
            if not grouped.empty:
                chart_path = generate_chart(grouped, "bar")
                if chart_path:
                    charts.append({"title": f"{primary_metric} by {primary_cat}", "chart_path": f"/static/{chart_path}"})

        if primary_metric and date_cols:
            trend_df = df[[date_cols[0], primary_metric]].copy()
            trend_df[date_cols[0]] = pd.to_datetime(trend_df[date_cols[0]], errors="coerce")
            trend_df[primary_metric] = pd.to_numeric(trend_df[primary_metric], errors="coerce")
            trend_df = trend_df.dropna()
            if len(trend_df) >= 2:
                span_days = (trend_df[date_cols[0]].max() - trend_df[date_cols[0]].min()).days
                freq = "D" if span_days <= 60 else ("W" if span_days <= 400 else "M")
                trend_grouped = trend_df.set_index(date_cols[0])[primary_metric].resample(freq).sum().reset_index()
                trend_grouped[date_cols[0]] = trend_grouped[date_cols[0]].dt.strftime("%Y-%m-%d")
                if len(trend_grouped) >= 2:
                    chart_path2 = generate_chart(trend_grouped, "line")
                    if chart_path2:
                        charts.append({"title": f"{primary_metric} Over Time", "chart_path": f"/static/{chart_path2}"})

        if not charts and primary_cat:
            # No numeric metric to chart by category - fall back to a plain record-count
            # distribution of the main category, still genuinely informative on its own.
            counts = df[primary_cat].value_counts().head(10).reset_index()
            counts.columns = [primary_cat, "Record Count"]
            if not counts.empty:
                chart_path3 = generate_chart(counts, "bar")
                if chart_path3:
                    charts.append({"title": f"Record Count by {primary_cat}", "chart_path": f"/static/{chart_path3}"})
    except Exception as chart_err:
        print("[AUTO KPI DASHBOARD] Chart generation notice:", chart_err)

    return {
        "success": True,
        "kpis": kpis,
        "charts": charts[:3],
        "primary_metric": primary_metric,
        "primary_category": primary_cat,
        "has_date_column": bool(date_cols),
    }


def generate_dynamic_dataset_suggestions(table_name: str) -> list:
    """
    Builds exactly 4 "Smart Dataset Suggestions" question pills tailored to this specific
    dataset's actual columns - replaces a previous version that only recognized a
    hardcoded "logistics" or "sales" domain by column-name keywords (and silently fell
    back to literal "Lahore return orders" style suggestions, written for one specific
    delivery dataset, for anything that didn't match). Reuses the SAME column-role
    detection already built for Ask AI / the auto-KPI dashboard (get_dataset_schema_
    intelligence + get_real_numeric_columns/get_real_categorical_columns) rather than a
    separate keyword-matching system, so a pharmacy dataset, a factory dataset, and a
    school attendance dataset each get their own genuinely relevant suggestions built
    from their own real columns and real data - not a fixed template.

    Every returned prompt is phrased so the existing deterministic Ask AI pipeline can
    answer it directly (same vocabulary as the fast pattern SQL generator expects:
    "Total X by Y", "Top N Y by X", "X trend over time"), since a suggestion that looks
    good but fails when clicked would be worse than no suggestion at all.

    Returns a list of up to 4 (label, prompt_text) tuples, in graceful order of
    sophistication - a dataset too small/plain for the richer ones (no clear metric, no
    date column) still gets simple, always-answerable fallbacks instead of erroring or
    showing something nonsensical.
    """
    import pandas as pd
    from ai.schema_intelligence import get_dataset_schema_intelligence
    from ai.metric_resolver import get_real_numeric_columns, get_real_categorical_columns
    from database.connection import run_query, sanitize_identifier

    try:
        schema_intel = get_dataset_schema_intelligence(table_name)
    except Exception:
        schema_intel = {}

    categorical_cols = get_real_categorical_columns(schema_intel) if schema_intel else []
    date_cols = schema_intel.get("date_columns", []) if schema_intel else []

    # schema_intel's classifier is sample-based and has previously misclassified a
    # string-ID-style column (e.g. "SKU1", "SKU2") as numeric - the auto-KPI dashboard
    # already hit this and fixed it the same way: re-validate each candidate against a
    # real sample of the actual column before trusting it, since a suggestion built on a
    # false-numeric column doesn't error, it just silently returns a meaningless NULL
    # total, which fails this feature's own "every suggestion must give a real answer"
    # requirement just as badly as a crash would.
    numeric_cols = []
    try:
        raw_numeric = get_real_numeric_columns(schema_intel) if schema_intel else []
        if raw_numeric:
            safe_tbl = sanitize_identifier(table_name)
            cols_sql = ", ".join(f"[{c}]" for c in raw_numeric)
            sample_df = run_query(f"SELECT TOP 500 {cols_sql} FROM {safe_tbl};")
            for c in raw_numeric:
                if c not in sample_df.columns:
                    continue
                coerced = pd.to_numeric(sample_df[c], errors="coerce")
                if coerced.notna().mean() >= 0.8:
                    numeric_cols.append(c)
    except Exception:
        numeric_cols = get_real_numeric_columns(schema_intel) if schema_intel else []

    metric = numeric_cols[0] if numeric_cols else None
    category = categorical_cols[0] if categorical_cols else None
    second_category = categorical_cols[1] if len(categorical_cols) > 1 else None
    date_col = date_cols[0] if date_cols else None

    suggestions = []

    # 1. Simple aggregate
    if metric and category:
        suggestions.append((f"📊 Total {metric} by {category}", f"Total {metric} by {category}"))
    elif category:
        suggestions.append((f"📊 Count by {category}", f"Count of records by {category}"))
    elif metric:
        suggestions.append((f"📊 Total {metric}", f"What is the total {metric}"))

    # 2. Time-based trend, only offered when the dataset actually has a date column
    if date_col and metric:
        suggestions.append((f"📈 {metric} Trend", f"Total {metric} by month"))
    elif date_col:
        suggestions.append(("📈 Records Over Time", "Count of records by month"))

    # 3. Ranking - uses the SECOND categorical column when available so this doesn't just
    # repeat suggestion #1 with "Top 5" prefixed onto the identical breakdown.
    rank_category = second_category or category
    if metric and rank_category:
        suggestions.append((f"🏆 Top 5 {rank_category}", f"Top 5 {rank_category} by {metric}"))
    elif metric:
        suggestions.append(("🏆 Top 10 Records", f"Top 10 records by {metric}"))

    # 4. A specific filtered question using a REAL value actually present in this
    # dataset (not a placeholder) - the most frequently occurring value in the primary
    # categorical column, so it's guaranteed to return real matching rows.
    if category and metric:
        try:
            safe_tbl = sanitize_identifier(table_name)
            safe_col = sanitize_identifier(category)
            top_df = run_query(
                f"SELECT TOP 1 {safe_col} AS val FROM {safe_tbl} WHERE {safe_col} IS NOT NULL "
                f"GROUP BY {safe_col} ORDER BY COUNT(*) DESC;"
            )
            if not top_df.empty:
                top_val = str(top_df.iloc[0]["val"]).strip()
                if top_val:
                    suggestions.append((f"🔍 {metric} for {top_val}", f"Total {metric} for {top_val}"))
        except Exception:
            pass

    # Fallbacks for a very simple/minimal dataset (no clear metric or date column) -
    # always-answerable, generic questions rather than erroring or showing nothing.
    if len(suggestions) < 4:
        if category:
            suggestions.append((f"🔍 Distinct {category}", f"Show all distinct values in {category}"))
    if len(suggestions) < 4:
        suggestions.append(("📋 Show Total Records", "Show total records"))
    if len(suggestions) < 4:
        suggestions.append(("🔍 Top 10 Rows", "Show top 10 rows from dataset"))

    # De-duplicate while preserving order, in case a small dataset's columns made two
    # branches above produce the same prompt text.
    seen = set()
    unique_suggestions = []
    for label, prompt in suggestions:
        if prompt not in seen:
            seen.add(prompt)
            unique_suggestions.append((label, prompt))

    return unique_suggestions[:4]
