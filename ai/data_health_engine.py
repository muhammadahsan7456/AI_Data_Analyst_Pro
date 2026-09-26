"""
Automated Data Profiling, Quality & Health Score Engine (0 - 100)
Calculates composite health metrics, category breakdowns, and audit alerts.
"""

import re
import pandas as pd
import numpy as np
from database.connection import run_query


def compute_dataset_health_score(df: pd.DataFrame) -> dict:
    """
    Computes a comprehensive 0 - 100 Data Health Score and detailed profile.
    """
    if df is None or df.empty:
        return {
            "score": 0,
            "rating": "Poor",
            "total_rows": 0,
            "total_columns": 0,
            "null_cells": 0,
            "null_percentage": 0.0,
            "duplicate_rows": 0,
            "duplicate_percentage": 0.0,
            "empty_columns": [],
            "constant_columns": [],
            "invalid_emails_count": 0,
            "outliers_count": 0,
            "breakdown": {
                "completeness": 0,
                "uniqueness": 0,
                "validity": 0,
                "consistency": 0
            },
            "alerts": ["Dataset is empty or could not be loaded."]
        }

    total_rows = len(df)
    total_cols = len(df.columns)
    total_cells = total_rows * total_cols

    # 1. COMPLETENESS (Nulls)
    null_cells = int(df.isnull().sum().sum())
    null_percentage = round((null_cells / total_cells) * 100, 2) if total_cells > 0 else 0.0
    completeness_score = max(0, round(100 - null_percentage, 1))

    # Empty & Constant Columns
    empty_columns = [col for col in df.columns if df[col].isnull().all()]
    constant_columns = [col for col in df.columns if df[col].nunique(dropna=True) == 1]

    # 2. UNIQUENESS (Duplicates)
    id_cols = [c for c in df.columns if any(kw in c.lower() for kw in ["id", "index", "recordid", "code"])]
    if id_cols:
        primary_id = id_cols[0]
        duplicate_ids = int(df.duplicated(subset=[primary_id]).sum())
    else:
        duplicate_ids = int(df.duplicated().sum())

    duplicate_percentage = round((duplicate_ids / total_rows) * 100, 2) if total_rows > 0 else 0.0
    uniqueness_score = max(0, round(100 - (duplicate_percentage * 2), 1))

    # 3. VALIDITY (Email & Phone syntax)
    invalid_emails = 0
    email_cols = [c for c in df.columns if "email" in c.lower()]
    email_regex = r"^[\w\.-]+@[\w\.-]+\.\w+$"
    for e_col in email_cols:
        for val in df[e_col].dropna():
            if not re.match(email_regex, str(val).strip()):
                invalid_emails += 1

    validity_deduction = (invalid_emails / total_rows * 50) if total_rows > 0 else 0
    validity_score = max(0, round(100 - validity_deduction, 1))

    # 4. CONSISTENCY & OUTLIERS (IQR Statistical Outliers)
    numeric_df = df.select_dtypes(include=[np.number])
    total_outliers = 0
    for n_col in numeric_df.columns:
        if n_col.lower() not in ["recordid", "index", "id"]:
            col_data = numeric_df[n_col].dropna()
            if len(col_data) >= 10:
                q1 = col_data.quantile(0.25)
                q3 = col_data.quantile(0.75)
                iqr = q3 - q1
                if iqr > 0:
                    outliers = col_data[(col_data < (q1 - 1.5 * iqr)) | (col_data > (q3 + 1.5 * iqr))]
                    total_outliers += len(outliers)

    outlier_percentage = (total_outliers / total_cells * 100) if total_cells > 0 else 0
    consistency_score = max(0, round(100 - (outlier_percentage * 3) - (len(constant_columns) * 5), 1))

    # COMPOSITE WEIGHTED SCORE
    composite_score = round(
        (completeness_score * 0.35) +
        (uniqueness_score * 0.30) +
        (validity_score * 0.20) +
        (consistency_score * 0.15),
        1
    )

    if composite_score >= 90:
        rating = "Excellent"
    elif composite_score >= 75:
        rating = "Good"
    elif composite_score >= 60:
        rating = "Needs Improvement"
    else:
        rating = "Poor"

    alerts = []
    if null_percentage > 5.0:
        alerts.append(f"High missing values detected ({null_percentage}% missing cells).")
    if duplicate_percentage > 1.0:
        alerts.append(f"Duplicate identifiers found ({duplicate_ids} duplicate records).")
    if empty_columns:
        alerts.append(f"Empty columns detected: {', '.join(empty_columns)}.")
    if constant_columns:
        alerts.append(f"Static constant columns detected: {', '.join(constant_columns)}.")
    if invalid_emails > 0:
        alerts.append(f"Formatting issues: {invalid_emails} invalid email addresses.")
    if total_outliers > 0:
        alerts.append(f"Statistical outliers detected: {total_outliers} numeric values exceed 1.5x IQR boundary.")

    if not alerts:
        alerts.append("No critical data quality issues detected. Dataset is clean and highly consistent.")

    return {
        "score": composite_score,
        "rating": rating,
        "total_rows": total_rows,
        "total_columns": total_cols,
        "null_cells": null_cells,
        "null_percentage": null_percentage,
        "duplicate_rows": duplicate_ids,
        "duplicate_percentage": duplicate_percentage,
        "empty_columns": empty_columns,
        "constant_columns": constant_columns,
        "invalid_emails_count": invalid_emails,
        "outliers_count": total_outliers,
        "breakdown": {
            "completeness": completeness_score,
            "uniqueness": uniqueness_score,
            "validity": validity_score,
            "consistency": consistency_score
        },
        "alerts": alerts
    }


def analyze_table_health(table_name: str) -> dict:
    """Fetches dataset sample from MS SQL Server and runs Health Profiler."""
    try:
        df = run_query(f"SELECT TOP 5000 * FROM [{table_name}]")
        return compute_dataset_health_score(df)
    except Exception:
        return compute_dataset_health_score(None)
