"""
Data Quality & Health Score Center
Calculates real dataset statistics, completeness, duplicates, consistency,
type validity, outliers, and generates categorized issues (Critical, Warning, Suggestion).
"""

import pandas as pd
import numpy as np
from database.connection import run_query, sanitize_identifier


def analyze_dataset_quality(table_name: str) -> dict:
    """
    Perform deep statistical data quality analysis on dataset.
    Returns:
        {
            "health_score": int (0-100),
            "completeness_score": int (0-100),
            "duplicates_score": int (0-100),
            "consistency_score": int (0-100),
            "datatype_score": int (0-100),
            "total_rows": N,
            "total_columns": N,
            "issues": [
                {
                    "type": "Critical" | "Warning" | "Suggestion",
                    "title": str,
                    "column": str,
                    "affected_records": int,
                    "percentage": float,
                    "explanation": str,
                    "suggested_fix": str
                }
            ],
            "issues_summary": {
                "critical_count": N,
                "warning_count": N,
                "suggestion_count": N
            }
        }
    """
    safe_tbl = sanitize_identifier(table_name)
    df = run_query(f"SELECT * FROM {safe_tbl};")

    if df is None or df.empty:
        return {
            "health_score": 0,
            "completeness_score": 0,
            "duplicates_score": 0,
            "consistency_score": 0,
            "datatype_score": 0,
            "total_rows": 0,
            "total_columns": 0,
            "issues": [],
            "issues_summary": {"critical_count": 0, "warning_count": 0, "suggestion_count": 0}
        }

    # Exclude technical RecordID column from analytics
    cols = [c for c in df.columns if c.lower() != "recordid"]
    df_clean = df[cols]

    total_rows = len(df_clean)
    total_cols = len(cols)
    total_cells = total_rows * total_cols if total_cols > 0 else 1

    issues = []

    # 1. Missing Values & Empty Columns (Completeness)
    null_cells = df_clean.isna().sum().sum()
    completeness_score = int(max(0, min(100, round((1 - (null_cells / total_cells)) * 100))))

    for col in cols:
        col_nulls = df_clean[col].isna().sum()
        if col_nulls > 0:
            pct = round((col_nulls / total_rows) * 100, 1)
            severity = "Critical" if pct >= 15.0 else ("Warning" if pct >= 3.0 else "Suggestion")
            issues.append({
                "type": severity,
                "title": f"Missing Values in {col}",
                "column": col,
                "affected_records": int(col_nulls),
                "percentage": pct,
                "explanation": f"Column '{col}' contains {col_nulls:,} missing (NULL) cells ({pct}% of dataset).",
                "suggested_fix": "Impute missing values using category mode or numerical mean/median."
            })

    # 2. Duplicate Rows (Duplicates Score)
    duplicate_rows = df_clean.duplicated().sum()
    duplicates_score = int(max(0, min(100, round((1 - (duplicate_rows / total_rows)) * 100))))
    if duplicate_rows > 0:
        pct = round((duplicate_rows / total_rows) * 100, 1)
        severity = "Critical" if pct >= 10.0 else "Warning"
        issues.append({
            "type": severity,
            "title": "Duplicate Records Detected",
            "column": "Entire Record",
            "affected_records": int(duplicate_rows),
            "percentage": pct,
            "explanation": f"Dataset contains {duplicate_rows:,} exact duplicate rows ({pct}% of dataset).",
            "suggested_fix": "Deduplicate dataset using primary entity ID or drop exact duplicate rows."
        })

    # 3. Data Consistency & Outlier Score
    outlier_count = 0
    consistency_penalties = 0
    for col in cols:
        if pd.api.types.is_numeric_dtype(df_clean[col]):
            series = df_clean[col].dropna()
            if len(series) > 10:
                mean = series.mean()
                std = series.std()
                if std > 0:
                    z_scores = ((series - mean) / std).abs()
                    col_outliers = (z_scores > 3.0).sum()
                    if col_outliers > 0:
                        outlier_count += col_outliers
                        pct = round((col_outliers / total_rows) * 100, 1)
                        issues.append({
                            "type": "Warning" if pct < 5.0 else "Critical",
                            "title": f"Numerical Outliers in {col}",
                            "column": col,
                            "affected_records": int(col_outliers),
                            "percentage": pct,
                            "explanation": f"Column '{col}' has {col_outliers} extreme values exceeding 3 standard deviations.",
                            "suggested_fix": "Cap extreme numerical outliers or apply log scaling."
                        })
        else:
            # Check for mixed formatting (numbers stored as string with mixed symbols)
            sample_str = df_clean[col].dropna().astype(str)
            mixed_num = sample_str.str.contains(r"^\$?[\d,]+(?:\.\d+)?$", regex=True).sum()
            if 0 < mixed_num < len(sample_str) * 0.8:
                consistency_penalties += 1
                issues.append({
                    "type": "Suggestion",
                    "title": f"Mixed Data Formatting in {col}",
                    "column": col,
                    "affected_records": int(mixed_num),
                    "percentage": round((mixed_num / total_rows) * 100, 1),
                    "explanation": f"Column '{col}' contains mixed text and numeric formats.",
                    "suggested_fix": "Standardize column formatting to uniform string or float data type."
                })

    consistency_score = int(max(0, min(100, 100 - (consistency_penalties * 5) - int((outlier_count / total_rows) * 50))))
    datatype_score = int(max(0, min(100, 100 - (consistency_penalties * 8))))

    # Overall Weighted Health Score (0-100)
    health_score = int(round(
        (completeness_score * 0.40) +
        (duplicates_score * 0.30) +
        (consistency_score * 0.15) +
        (datatype_score * 0.15)
    ))

    issues_summary = {
        "critical_count": sum(1 for i in issues if i["type"] == "Critical"),
        "warning_count": sum(1 for i in issues if i["type"] == "Warning"),
        "suggestion_count": sum(1 for i in issues if i["type"] == "Suggestion")
    }

    return {
        "health_score": health_score,
        "completeness_score": completeness_score,
        "duplicates_score": duplicates_score,
        "consistency_score": consistency_score,
        "datatype_score": datatype_score,
        "total_rows": total_rows,
        "total_columns": total_cols,
        "issues": issues,
        "issues_summary": issues_summary
    }
