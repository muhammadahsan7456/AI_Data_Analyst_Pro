"""
Structured Dataset Context & Schema Metadata Engine
Extracts detailed dataset metadata, column data types, min/max, null counts,
sample values, and low-cardinality distinct values for high-precision AI SQL generation.
"""

import pandas as pd
from database.connection import run_query, sanitize_identifier


def extract_dataset_structured_context(table_name: str, max_samples: int = 5) -> dict:
    """
    Collect structured metadata about the dataset without sending full raw data.
    Returns:
        {
            "table_name": table_name,
            "total_records": N,
            "columns_metadata": [
                {
                    "name": col,
                    "type": dtype,
                    "null_count": int,
                    "sample_values": [val1, val2],
                    "distinct_count": int,
                    "distinct_values": [val1, val2] (for low cardinality <= 25),
                    "min_val": str/num,
                    "max_val": str/num
                }
            ]
        }
    """
    safe_tbl = sanitize_identifier(table_name)
    context = {
        "table_name": table_name,
        "total_records": 0,
        "columns_metadata": []
    }

    try:
        # Fetch total count
        count_df = run_query(f"SELECT COUNT(*) AS [TotalRows] FROM {safe_tbl};")
        if not count_df.empty:
            context["total_records"] = int(count_df.iloc[0]["TotalRows"])

        # Fetch sample rows (top 50) for fast metadata estimation
        sample_df = run_query(f"SELECT TOP 50 * FROM {safe_tbl};")
        if sample_df.empty:
            return context

        for col in sample_df.columns:
            col_safe = sanitize_identifier(col)
            col_dtype = str(sample_df[col].dtype)
            col_samples = sample_df[col].dropna().unique()[:max_samples].tolist()
            col_samples_clean = [str(v) for v in col_samples if v is not None]

            col_meta = {
                "name": col,
                "type": col_dtype,
                "null_count": int(sample_df[col].isna().sum()),
                "sample_values": col_samples_clean,
                "distinct_count": len(sample_df[col].unique()),
                "distinct_values": [],
                "min_val": None,
                "max_val": None
            }

            # If categorical or text column with low cardinality, fetch distinct values directly from database
            if not pd.api.types.is_numeric_dtype(sample_df[col]):
                try:
                    dist_df = run_query(f"SELECT DISTINCT TOP 25 {col_safe} FROM {safe_tbl} WHERE {col_safe} IS NOT NULL;")
                    if not dist_df.empty:
                        distinct_vals = dist_df.iloc[:, 0].dropna().astype(str).tolist()
                        col_meta["distinct_values"] = distinct_vals
                        col_meta["distinct_count"] = len(distinct_vals)
                except Exception:
                    pass
            else:
                # Numeric min/max
                try:
                    stats_df = run_query(f"SELECT MIN({col_safe}) AS [MinVal], MAX({col_safe}) AS [MaxVal] FROM {safe_tbl};")
                    if not stats_df.empty:
                        col_meta["min_val"] = stats_df.iloc[0]["MinVal"]
                        col_meta["max_val"] = stats_df.iloc[0]["MaxVal"]
                except Exception:
                    pass

            context["columns_metadata"].append(col_meta)

    except Exception as err:
        print("Context Engine Extraction Error:", err)

    return context


def format_context_prompt_text(context: dict) -> str:
    """
    Format structured context into concise, highly readable prompt text for Gemini/AI.
    """
    if not context or not context.get("columns_metadata"):
        return ""

    lines = [
        f"DATASET TABLE: [{context.get('table_name')}] (Total Records: {context.get('total_records', 0)})",
        "STRUCTURED COLUMN SCHEMA METADATA:"
    ]

    for col in context.get("columns_metadata", []):
        meta_str = f" - Column: [{col['name']}] | Type: {col['type']}"
        if col.get("sample_values"):
            meta_str += f" | Examples: {', '.join(col['sample_values'][:4])}"
        if col.get("distinct_values"):
            meta_str += f" | Valid Distinct Values: {', '.join(col['distinct_values'][:12])}"
        if col.get("min_val") is not None and col.get("max_val") is not None:
            meta_str += f" | Range: [{col['min_val']} to {col['max_val']}]"
        lines.append(meta_str)

    return "\n".join(lines)
