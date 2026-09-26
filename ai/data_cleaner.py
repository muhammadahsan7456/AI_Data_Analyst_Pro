"""
Data Cleaning Suite Module
Safe, non-destructive dataset cleaning operations:
- Duplicate row removal
- Missing value imputation (Mean/Median/Mode)
- Text whitespace trimming & casing standardization
- Invalid value correction
"""

import pandas as pd
import numpy as np


def clean_dataframe(df: pd.DataFrame, options: dict = None) -> tuple:
    """
    Cleans DataFrame based on selected user options.
    Returns (cleaned_df, summary_stats)
    """
    if df is None or df.empty:
        return df, {"rows_removed": 0, "nulls_filled": 0, "trimmed_cells": 0}

    if options is None:
        options = {
            "drop_duplicates": True,
            "fill_nulls": True,
            "trim_whitespace": True
        }

    cleaned_df = df.copy()
    initial_rows = len(cleaned_df)
    nulls_filled = 0
    trimmed_cells = 0

    # 1. DROP DUPLICATES
    if options.get("drop_duplicates"):
        non_pk_cols = [c for c in cleaned_df.columns if c.lower() not in ["recordid", "index"]]
        cleaned_df = cleaned_df.drop_duplicates(subset=non_pk_cols if non_pk_cols else None)

    rows_removed = initial_rows - len(cleaned_df)

    # 2. TRIM WHITESPACE & STANDARDIZE TEXT
    if options.get("trim_whitespace"):
        string_cols = cleaned_df.select_dtypes(include=["object", "string"]).columns
        for s_col in string_cols:
            before_vals = cleaned_df[s_col].astype(str)
            cleaned_df[s_col] = cleaned_df[s_col].apply(lambda x: str(x).strip() if pd.notnull(x) and str(x) != "nan" else x)
            after_vals = cleaned_df[s_col].astype(str)
            trimmed_cells += int((before_vals != after_vals).sum())

    # 3. FILL MISSING VALUES
    if options.get("fill_nulls"):
        numeric_cols = cleaned_df.select_dtypes(include=[np.number]).columns
        for n_col in numeric_cols:
            if n_col.lower() not in ["recordid", "index"]:
                n_nulls = int(cleaned_df[n_col].isnull().sum())
                if n_nulls > 0:
                    median_val = cleaned_df[n_col].median()
                    cleaned_df[n_col] = cleaned_df[n_col].fillna(median_val)
                    nulls_filled += n_nulls

        object_cols = cleaned_df.select_dtypes(include=["object", "string"]).columns
        for o_col in object_cols:
            o_nulls = int(cleaned_df[o_col].isnull().sum())
            if o_nulls > 0:
                mode_val = cleaned_df[o_col].mode()
                fill_val = mode_val.iloc[0] if not mode_val.empty else "N/A"
                cleaned_df[o_col] = cleaned_df[o_col].fillna(fill_val)
                nulls_filled += o_nulls

    summary = {
        "initial_rows": initial_rows,
        "final_rows": len(cleaned_df),
        "rows_removed": rows_removed,
        "nulls_filled": nulls_filled,
        "trimmed_cells": trimmed_cells
    }

    return cleaned_df, summary
