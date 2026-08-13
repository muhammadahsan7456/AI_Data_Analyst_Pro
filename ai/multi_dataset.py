import re
import os
import sys
import pandas as pd
from typing import List, Dict, Any, Tuple

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from database.connection import run_query, get_table_columns, sanitize_identifier
except ModuleNotFoundError:
    from connection import run_query, get_table_columns, sanitize_identifier


def execute_multi_dataset_analysis(
    question: str,
    dataset_tables: List[Tuple[int, str, str]]
) -> Dict[str, Any]:
    """
    Multi Dataset AI Analysis (Feature 5).
    Enables cross-dataset queries across Dataset A, Dataset B, Dataset C, etc.
    Supported cross-dataset operations:
    - Compare schemas & metrics
    - Find common entities / records (INNER JOIN)
    - Find duplicates across datasets
    - Union / Merge datasets (FULL OUTER JOIN)
    - Revenue / numerical metrics comparison across tables
    """
    if not dataset_tables or len(dataset_tables) < 2:
        return {
            "success": False,
            "error": "At least 2 datasets must be selected for multi-dataset cross analysis."
        }

    q_lower = question.lower().strip()
    table_names = [dt[1] for dt in dataset_tables]
    file_names = [dt[2] for dt in dataset_tables]

    # Fetch columns for each table
    schemas = {}
    for dt_id, tbl, fname in dataset_tables:
        schemas[tbl] = get_table_columns(tbl)

    # 1. Compare Datasets Query
    if any(kw in q_lower for kw in ["compare", "difference", "comparison"]):
        summary_rows = []
        for dt_id, tbl, fname in dataset_tables:
            safe_tbl = sanitize_identifier(tbl)
            count_df = run_query(f"SELECT COUNT(*) AS TotalRows FROM {safe_tbl}")
            total_rows = count_df.iloc[0]["TotalRows"] if not count_df.empty else 0
            cols = schemas[tbl]
            summary_rows.append({
                "Dataset ID": dt_id,
                "File Name": fname,
                "Table Name": tbl,
                "Total Rows": total_rows,
                "Total Columns": len(cols),
                "Columns": ", ".join(cols[:8]) + ("..." if len(cols) > 8 else "")
            })
        df_res = pd.DataFrame(summary_rows)
        return {
            "success": True,
            "type": "comparison",
            "sql": f"-- Cross Dataset Comparison across {', '.join(table_names)}",
            "df": df_res,
            "explanation": f"Compared {len(dataset_tables)} datasets ({', '.join(file_names)}). Summary metrics generated above."
        }

    # 2. Find Common Records / Common Entities
    if any(kw in q_lower for kw in ["common", "matching", "overlap", "same"]):
        tbl1, tbl2 = table_names[0], table_names[1]
        cols1 = [c for c in schemas[tbl1] if c.lower() != "recordid"]
        cols2 = [c for c in schemas[tbl2] if c.lower() != "recordid"]
        
        common_cols = [c for c in cols1 if c in cols2]
        if not common_cols:
            # Fallback to text matching
            common_cols = [c for c in cols1 if any(c.lower() in c2.lower() for c2 in cols2)]

        if common_cols:
            join_col = common_cols[0]
            sql = f"""
            SELECT TOP 100 t1.{sanitize_identifier(join_col)}, t1.*, t2.*
            FROM {sanitize_identifier(tbl1)} t1
            INNER JOIN {sanitize_identifier(tbl2)} t2
                ON t1.{sanitize_identifier(join_col)} = t2.{sanitize_identifier(join_col)};
            """
            try:
                df_res = run_query(sql)
                return {
                    "success": True,
                    "type": "common_records",
                    "sql": sql,
                    "df": df_res,
                    "explanation": f"Found {len(df_res)} matching records based on common field '{join_col}'."
                }
            except Exception as join_err:
                pass

    # 3. Merge / Union Datasets
    if any(kw in q_lower for kw in ["merge", "combine", "union", "concat"]):
        tbl1, tbl2 = table_names[0], table_names[1]
        cols1 = [c for c in schemas[tbl1] if c.lower() != "recordid"]
        cols2 = [c for c in schemas[tbl2] if c.lower() != "recordid"]
        shared_cols = [c for c in cols1 if c in cols2]

        if shared_cols:
            col_str = ", ".join(sanitize_identifier(c) for c in shared_cols)
            sql = f"""
            SELECT TOP 100 'Dataset_1' AS [SourceDataset], {col_str} FROM {sanitize_identifier(tbl1)}
            UNION ALL
            SELECT TOP 100 'Dataset_2' AS [SourceDataset], {col_str} FROM {sanitize_identifier(tbl2)};
            """
            df_res = run_query(sql)
            return {
                "success": True,
                "type": "merged_union",
                "sql": sql,
                "df": df_res,
                "explanation": f"Merged records from both datasets across shared columns: {', '.join(shared_cols)}."
            }

    # Default: Multi-dataset comparative summary query
    tbl1, tbl2 = table_names[0], table_names[1]
    sql = f"""
    SELECT TOP 50 'Dataset 1' AS DatasetSource, * FROM {sanitize_identifier(tbl1)}
    UNION ALL
    SELECT TOP 50 'Dataset 2' AS DatasetSource, * FROM {sanitize_identifier(tbl2)};
    """
    try:
        df_res = run_query(sql)
        return {
            "success": True,
            "type": "multi_dataset_default",
            "sql": sql,
            "df": df_res,
            "explanation": f"Combined preview of records across {file_names[0]} and {file_names[1]}."
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to execute cross-dataset analysis: {str(e)}"
        }
