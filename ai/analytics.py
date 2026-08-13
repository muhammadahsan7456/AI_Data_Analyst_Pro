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
    Compute overall data quality summary and detailed statistics.
    """
    total_cells = df.size
    total_missing = int(df.isnull().sum().sum())
    total_duplicates = int(df.duplicated().sum())

    health_score = 100.0
    if total_cells > 0:
        missing_penalty = (total_missing / total_cells) * 40
        health_score -= missing_penalty
    if len(df) > 0:
        dup_penalty = (total_duplicates / len(df)) * 20
        health_score -= dup_penalty

    return {
        "health_score": max(0.0, round(health_score, 1)),
        "total_rows": len(df),
        "total_cols": len(df.columns),
        "total_missing": total_missing,
        "total_duplicates": total_duplicates,
        "column_profiles": get_column_profiling(df),
        "outliers": detect_outliers_iqr(df)
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
