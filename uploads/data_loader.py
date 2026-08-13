import os
import sys
import re
import gc
import math
import pandas as pd
from pandas.api.types import (
    is_integer_dtype,
    is_float_dtype,
    is_bool_dtype,
    is_datetime64_any_dtype
)

# Ensure workspace root is in sys.path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from database.connection import get_connection, get_db_cursor, sanitize_identifier
except ModuleNotFoundError:
    from connection import get_connection, get_db_cursor, sanitize_identifier


def optimize_dataframe_memory(df: pd.DataFrame) -> pd.DataFrame:
    """
    Downcast numerical types and convert low-cardinality text columns to category types
    to minimize memory footprint for 100k, 500k, and 1M row datasets.
    """
    if df is None or df.empty:
        return df

    for col in df.columns:
        col_type = df[col].dtype

        if is_integer_dtype(df[col]):
            df[col] = pd.to_numeric(df[col], downcast="integer")
        elif is_float_dtype(df[col]):
            df[col] = pd.to_numeric(df[col], downcast="float")
        elif col_type == "object":
            num_unique = df[col].nunique()
            num_total = len(df[col])
            if num_total > 0 and (num_unique / num_total) < 0.3:
                df[col] = df[col].astype("category")

    return df


def clean_table_name(file_name: str, user_id: int = None) -> str:
    """
    Convert file name into a clean, safe SQL table name isolated per UserID.
    """
    base_name = file_name.rsplit(".", 1)[0]
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", base_name).strip("_")
    
    # Prepend TBL_ if starts with digit
    if cleaned and cleaned[0].isdigit():
        cleaned = f"TBL_{cleaned}"
        
    if not cleaned:
        cleaned = "UploadedDataset"

    if user_id:
        cleaned = f"{cleaned[:80]}_u{user_id}"
        
    return cleaned[:100]


def infer_smart_column_name(series: pd.Series, current_name: str, index: int = 0) -> str:
    """
    Auto-detect and infer smart human-readable column headers for Unnamed: X, Unnamed__X,
    blank, or Field_X columns by inspecting sample row values.
    """
    col_str = str(current_name).strip() if current_name is not None else ""
    c_lower = col_str.lower()

    # Check if header needs intelligent auto-naming
    is_unnamed = (
        not col_str or
        c_lower.startswith("unnamed") or
        c_lower.startswith("field_") or
        c_lower.startswith("col_") or
        c_lower in ["none", "null", "nan", "column", ""]
    )

    if not is_unnamed:
        # Already has a clean user-provided column name! Just sanitize formatting.
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", col_str).strip("_")
        cleaned = re.sub(r"_+", "_", cleaned)
        if not cleaned or cleaned.lower().startswith("unnamed"):
            is_unnamed = True
        else:
            if cleaned[0].isdigit():
                cleaned = f"Col_{cleaned}"
            return cleaned

    # Inspect non-null values to infer business name
    non_null = series.dropna().astype(str).str.strip()
    non_null = non_null[non_null != ""]

    if non_null.empty:
        return f"Attribute_{index + 1}"

    sample = non_null.head(30)

    # 1. Date Detection (e.g., 2025-07-26, 07/26/2025, 2025/07/26)
    date_matches = sample.str.contains(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$|^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$", regex=True).mean()
    if date_matches > 0.5:
        return "Order_Date"

    # 2. Order ID / Invoice Code Detection (e.g., #1006, #1005, ORD-101, INV-202)
    id_matches = sample.str.contains(r"^#\d+|^[A-Z]{2,4}-\d+|^ORD\d+|^INV\d+", regex=True).mean()
    if id_matches > 0.3:
        return "Order_ID"

    # 3. Scientific Notation / Long Tracking ID / Account Number (e.g., 2.703008e+13, 98402914092)
    track_matches = sample.str.contains(r"e\+\d+|\d{10,}", regex=True, case=False).mean()
    if track_matches > 0.3:
        return "Tracking_ID"

    # 4. Status Detection (e.g., Delivered, Shipped, Pending, Returned, Completed, Active)
    status_keywords = {"delivered", "shipped", "pending", "returned", "completed", "active", "cancelled", "paid", "unpaid", "success", "failed"}
    status_matches = sample.str.lower().isin(status_keywords).mean()
    if status_matches > 0.3:
        return "Delivery_Status"

    # 5. Email Detection
    email_matches = sample.str.contains(r"^[\w\.-]+@[\w\.-]+\.\w+$", regex=True).mean()
    if email_matches > 0.5:
        return "Email_Address"

    # 6. Currency / Price / Amount Detection (e.g., 2200.0, 3000.0, $150.00)
    numeric_series = pd.to_numeric(sample.str.replace(r"[$,]", "", regex=True), errors="coerce")
    if numeric_series.notna().mean() > 0.7:
        if (numeric_series > 0).all():
            return "Amount"
        return "Metric"

    # 7. Name / Person Detection
    name_matches = sample.str.contains(r"^[A-Za-z\s.'-]+$", regex=True).mean()
    if name_matches > 0.7:
        return "Customer_Name" if index <= 2 else f"Category_{index + 1}"

    return f"Field_{index + 1}"


def auto_repair_dataframe_headers(dataframe: pd.DataFrame) -> pd.DataFrame:
    """
    Intelligently repair, auto-promote, and infer human-readable column headers for any DataFrame.
    """
    if dataframe is None or dataframe.empty:
        return dataframe

    df = dataframe.copy()

    # Step 1: Row 0 Header Auto-Promotion if columns are mostly Unnamed & Row 0 has title words
    unnamed_cols = [c for c in df.columns if not str(c).strip() or "unnamed" in str(c).lower() or str(c).lower().startswith("field_")]
    if len(unnamed_cols) / max(len(df.columns), 1) >= 0.3 and len(df) > 1:
        row0_vals = [str(val).strip() for val in df.iloc[0]]
        header_keywords = {"date", "id", "name", "status", "price", "amount", "qty", "quantity", "total", "category", "city", "country", "code", "description", "title", "user", "customer"}
        row0_lower = [v.lower() for v in row0_vals]
        has_header_words = any(any(kw in v for kw in header_keywords) for v in row0_lower)
        has_dates_or_amounts = any(
            re.search(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$|^\d+\.\d+$|^#\d+", v)
            for v in row0_vals if v
        )

        if has_header_words and not has_dates_or_amounts:
            new_cols = []
            for i, val in enumerate(row0_vals):
                val_clean = str(val).strip()
                if not val_clean or val_clean.lower() in ["none", "null", "nan"]:
                    val_clean = f"Attribute_{i + 1}"
                new_cols.append(val_clean)
            df.columns = new_cols
            df = df.iloc[1:].reset_index(drop=True)

    # Step 2: Smart Intelligent Column Renaming using Value Inferencing
    new_columns = []
    used_names = set()

    for idx, col in enumerate(df.columns):
        if str(col).lower() == "recordid":
            smart_name = "RecordID"
        else:
            series = df[col]
            smart_name = infer_smart_column_name(series, col, index=idx)

        final_name = smart_name
        dup_cnt = 2
        while final_name in used_names:
            final_name = f"{smart_name}_{dup_cnt}"
            dup_cnt += 1

        used_names.add(final_name)
        new_columns.append(final_name)

    df.columns = new_columns
    return df


def clean_column_name(column_name: str, index: int = 0) -> str:
    """
    Clean column names to prevent SQL syntax errors and fix Unnamed/blank headers.
    """
    col_str = str(column_name).strip() if column_name is not None else ""
    c_lower = col_str.lower()
    
    if not col_str or c_lower.startswith("unnamed") or c_lower in ["none", "null", "nan"]:
        return f"Field_{index + 1}"

    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", col_str).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned or cleaned.lower().startswith("unnamed"):
        cleaned = f"Field_{index + 1}"
    if cleaned[0].isdigit():
        cleaned = f"Col_{cleaned}"
    return cleaned


def detect_column_type(series: pd.Series) -> str:
    """
    Detect optimal SQL Server data type for a Pandas series.
    """
    non_null_series = series.dropna()

    if len(non_null_series) == 0:
        return "NVARCHAR(MAX)"

    if is_bool_dtype(non_null_series):
        return "BIT"

    if is_integer_dtype(non_null_series):
        max_val = non_null_series.max()
        min_val = non_null_series.min()
        if min_val >= -2147483648 and max_val <= 2147483647:
            return "INT"
        return "BIGINT"

    if is_float_dtype(non_null_series):
        return "DECIMAL(18,2)"

    if is_datetime64_any_dtype(non_null_series):
        return "DATETIME2"

    # Try parsing date strings
    try:
        if non_null_series.dtype in ['object', 'category'] and len(non_null_series) > 0:
            sample = non_null_series.head(50)
            pd.to_datetime(sample, errors="raise")
            return "DATETIME2"
    except Exception:
        pass

    # Try numeric conversion
    numeric = pd.to_numeric(non_null_series, errors="coerce")
    if numeric.notna().mean() > 0.95:
        if (numeric.dropna() % 1 == 0).all():
            return "INT"
        return "DECIMAL(18,2)"

    return "NVARCHAR(MAX)"


def generate_create_table_query(table_name: str, dataframe: pd.DataFrame) -> str:
    """
    Generate SQL CREATE TABLE query dynamically.
    """
    columns_sql = []
    
    # Rename columns in dataframe copy for safe processing
    dataframe.columns = [clean_column_name(col) for col in dataframe.columns]

    for column in dataframe.columns:
        sql_type = detect_column_type(dataframe[column])
        columns_sql.append(f"{sanitize_identifier(column)} {sql_type}")

    columns_body = ",\n        ".join(columns_sql)
    safe_table = sanitize_identifier(table_name)

    query = f"""
    IF OBJECT_ID('{table_name}', 'U') IS NOT NULL
        DROP TABLE {safe_table};

    CREATE TABLE {safe_table}
    (
        RecordID INT IDENTITY(1,1) PRIMARY KEY,
        {columns_body}
    );
    """
    return query


def create_table(table_name: str, dataframe: pd.DataFrame):
    """
    Create SQL table dynamically.
    """
    query = generate_create_table_query(table_name, dataframe)
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(query)


def clean_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    """
    Clean DataFrame before inserting into SQL Server.
    """
    df = dataframe.copy()
    df.columns = [clean_column_name(col) for col in df.columns]

    # Replace NaN, NaT, Inf with None for PyODBC compatibility
    df = df.where(pd.notnull(df), None)

    for col in df.columns:
        if df[col].dtype in ["object", "category"]:
            df[col] = df[col].apply(
                lambda x: str(x).strip() if x is not None and not (isinstance(x, float) and math.isnan(x)) else None
            )

    return df


def insert_dataframe(table_name: str, dataframe: pd.DataFrame):
    """
    Insert DataFrame into SQL Server safely using parameterized batching and garbage collection.
    """
    df = clean_dataframe(dataframe)
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.fast_executemany = True

    columns = ", ".join(sanitize_identifier(col) for col in df.columns)
    placeholders = ", ".join("?" for _ in df.columns)
    safe_table = sanitize_identifier(table_name)

    query = f"INSERT INTO {safe_table} ({columns}) VALUES ({placeholders})"

    # Prepare rows as tuples
    rows_data = []
    for _, row in df.iterrows():
        tuple_row = []
        for val in row:
            if val is None or (isinstance(val, float) and math.isnan(val)):
                tuple_row.append(None)
            elif isinstance(val, bool):
                tuple_row.append(bool(val))
            elif isinstance(val, (int, float)):
                tuple_row.append(val)
            else:
                tuple_row.append(str(val))
        rows_data.append(tuple(tuple_row))

    batch_size = 5000
    try:
        for i in range(0, len(rows_data), batch_size):
            batch = rows_data[i:i + batch_size]
            cursor.executemany(query, batch)
        conn.commit()
    except Exception as e:
        conn.rollback()
        # Fallback to standard execution if fast_executemany fails
        for tuple_row in rows_data:
            cursor.execute(query, tuple_row)
        conn.commit()
    finally:
        cursor.close()
        conn.close()
        del rows_data
        gc.collect()