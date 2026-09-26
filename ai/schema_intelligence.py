"""
Dataset Schema Intelligence Layer
Extracts, structures, and caches rich metadata for uploaded dataset tables:
- Column names, original names, SQL-safe names
- SQL types & pandas/semantic data types (categorical, numeric, date, id, text)
- Distinct value samples (e.g. Country: ['Korea', 'India', 'Congo'])
- Numeric/Date ranges (min, max)
- Null cell counts & completeness percentages
"""

import os
import sys
import re
import pandas as pd
from database.connection import run_query, sanitize_identifier
from utils.cache import system_cache

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

ID_PATTERNS = [
    r"^recordid$", r"^id$", r".*_id$", r"^key$", r"^tracking_id$", r"^sr$",
    r"^s\.no$", r"^s_no$", r"^sno$", r"^index$", r"^row_num$", r"^guid$", r"^uuid$"
]


def classify_column_semantic_type(col_name: str, series: pd.Series) -> str:
    """
    Classifies a column into semantic types: id, date, numeric, categorical, or text.
    """
    c_lower = col_name.lower().strip()
    if any(re.match(p, c_lower) for p in ID_PATTERNS):
        return "id"

    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"

    if pd.api.types.is_numeric_dtype(series):
        if "year" in c_lower or "zip" in c_lower or "code" in c_lower:
            return "categorical"
        return "numeric"

    # String type classification
    non_nulls = series.dropna().astype(str)
    if non_nulls.empty:
        return "text"

    # Test date parsing
    if any(kw in c_lower for kw in ["date", "time", "dob", "created", "updated", "timestamp"]):
        return "date"

    distinct_cnt = non_nulls.nunique()
    if distinct_cnt <= 100 or (len(non_nulls) > 0 and (distinct_cnt / len(non_nulls)) < 0.2):
        return "categorical"

    return "text"


def get_dataset_schema_intelligence(table_name: str, use_cache: bool = True) -> dict:
    """
    Retrieves rich dataset schema metadata with caching.
    Scans sample records to build distinct value maps without scanning 500,000 rows repeatedly.
    """
    safe_tbl = sanitize_identifier(table_name)
    cache_key = f"schema_intel_{safe_tbl}"

    if use_cache and system_cache:
        cached = system_cache.get(cache_key)
        if cached:
            return cached

    # Fetch total row count & columns
    count_df = run_query(f"SELECT COUNT(*) AS TotalRows FROM {safe_tbl};")
    total_rows = int(count_df.iloc[0]["TotalRows"]) if count_df is not None and not count_df.empty else 0

    # Sample top 300 rows for high-speed statistical profiling
    sample_df = run_query(f"SELECT TOP 300 * FROM {safe_tbl};")
    if sample_df is None or sample_df.empty:
        return {
            "table_name": table_name,
            "total_rows": total_rows,
            "total_columns": 0,
            "columns": {},
            "column_list": [],
            "text_columns": [],
            "numeric_columns": [],
            "date_columns": [],
            "categorical_columns": {}
        }

    columns_meta = {}
    column_list = list(sample_df.columns)
    text_cols = []
    numeric_cols = []
    date_cols = []
    categorical_map = {}

    for col in column_list:
        series = sample_df[col]
        sem_type = classify_column_semantic_type(col, series)

        distinct_vals = []
        min_val = None
        max_val = None

        if sem_type in ["categorical", "text"]:
            distinct_vals = series.dropna().astype(str).str.strip().unique()[:30].tolist()
            text_cols.append(col)
            if sem_type == "categorical":
                categorical_map[col] = distinct_vals
        elif sem_type == "numeric":
            numeric_cols.append(col)
            valid_num = pd.to_numeric(series, errors="coerce").dropna()
            if not valid_num.empty:
                min_val = float(valid_num.min())
                max_val = float(valid_num.max())
        elif sem_type == "date":
            date_cols.append(col)
            valid_str = series.dropna().astype(str)
            if not valid_str.empty:
                min_val = str(valid_str.min())[:10]
                max_val = str(valid_str.max())[:10]

        null_cnt = int(series.isna().sum())

        columns_meta[col] = {
            "column_name": col,
            "sql_safe_name": f"[{col}]",
            "semantic_type": sem_type,
            "sample_values": distinct_vals,
            "min_value": min_val,
            "max_value": max_val,
            "null_count": null_cnt,
            "distinct_count": len(distinct_vals) if distinct_vals else series.nunique()
        }

    schema_intel = {
        "table_name": table_name,
        "total_rows": total_rows,
        "total_columns": len(column_list),
        "columns": columns_meta,
        "column_list": column_list,
        "text_columns": text_cols,
        "numeric_columns": numeric_cols,
        "date_columns": date_cols,
        "categorical_columns": categorical_map
    }

    if system_cache:
        system_cache.set(cache_key, schema_intel, ttl=3600)

    return schema_intel
