import re
import os
import sys
import pandas as pd
from typing import List, Dict, Any, Tuple

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from database.connection import run_query, get_table_columns, sanitize_identifier
    from utils.error_translator import format_user_friendly_error
except ModuleNotFoundError:
    from connection import run_query, get_table_columns, sanitize_identifier
    from utils.error_translator import format_user_friendly_error


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
            "error": "Please select at least 2 datasets to execute cross-dataset analysis."
        }

    q_lower = question.lower().strip()
    table_names = [dt[1] for dt in dataset_tables]
    file_names = [dt[2] for dt in dataset_tables]

    # Fetch columns for each table
    schemas = {}
    for dt_id, tbl, fname in dataset_tables:
        schemas[tbl] = get_table_columns(tbl)

    try:
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
            cols1 = [c for c in schemas[tbl1] if c.lower() not in ["recordid", "s.no"]]
            cols2 = [c for c in schemas[tbl2] if c.lower() not in ["recordid", "s.no"]]
            
            common_cols = [c for c in cols1 if c in cols2]
            if not common_cols:
                common_cols = [c for c in cols1 if any(c.lower() in c2.lower() for c2 in cols2)]

            if common_cols:
                join_col = common_cols[0]
                sql = f"""
                SELECT TOP 100 t1.{sanitize_identifier(join_col)}, t1.*, t2.*
                FROM {sanitize_identifier(tbl1)} t1
                INNER JOIN {sanitize_identifier(tbl2)} t2
                    ON t1.{sanitize_identifier(join_col)} = t2.{sanitize_identifier(join_col)};
                """
                df_res = run_query(sql)
                return {
                    "success": True,
                    "type": "common_records",
                    "sql": sql,
                    "df": df_res,
                    "explanation": f"Found {len(df_res)} matching records based on common field '{join_col}'."
                }

        # 3. Merge / Union Datasets across Shared Columns
        tbl1, tbl2 = table_names[0], table_names[1]
        cols1 = [c for c in schemas[tbl1] if c.lower() not in ["recordid", "s.no"]]
        cols2 = [c for c in schemas[tbl2] if c.lower() not in ["recordid", "s.no"]]
        shared_cols = [c for c in cols1 if c in cols2]

        if shared_cols:
            col_str = ", ".join(sanitize_identifier(c) for c in shared_cols)
            sql = f"""
            SELECT TOP 100 '{file_names[0]}' AS [SourceDataset], {col_str} FROM {sanitize_identifier(tbl1)}
            UNION ALL
            SELECT TOP 100 '{file_names[1]}' AS [SourceDataset], {col_str} FROM {sanitize_identifier(tbl2)};
            """
            df_res = run_query(sql)
            return {
                "success": True,
                "type": "merged_union",
                "sql": sql,
                "df": df_res,
                "explanation": f"Merged records from both datasets across shared columns: {', '.join(shared_cols)}."
            }

        # 4. Fallback for Datasets with Different Structures: Return Comparative Schema & Sample Preview
        summary_rows = []
        for dt_id, tbl, fname in dataset_tables:
            safe_tbl = sanitize_identifier(tbl)
            count_df = run_query(f"SELECT COUNT(*) AS TotalRows FROM {safe_tbl}")
            total_rows = count_df.iloc[0]["TotalRows"] if not count_df.empty else 0
            cols = schemas[tbl]
            summary_rows.append({
                "Dataset ID": dt_id,
                "File Name": fname,
                "Total Rows": total_rows,
                "Total Columns": len(cols),
                "Available Fields": ", ".join(cols[:8]) + ("..." if len(cols) > 8 else "")
            })
        df_res = pd.DataFrame(summary_rows)
        return {
            "success": True,
            "type": "structural_comparison",
            "sql": f"-- Structural Comparison between [{file_names[0]}] and [{file_names[1]}]",
            "df": df_res,
            "explanation": f"Datasets [{file_names[0]}] and [{file_names[1]}] have different column structures. Generated side-by-side dataset comparison overview."
        }

    except Exception as e:
        return {
            "success": False,
            "error": format_user_friendly_error(e)
        }
