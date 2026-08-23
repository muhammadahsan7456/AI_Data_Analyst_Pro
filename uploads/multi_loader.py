import os
import sys
import io
import re
import gc
import math
import json
import pandas as pd
from typing import Tuple, Dict, Any, List, Optional

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from database.connection import get_db_cursor, sanitize_identifier
    from database.queries import insert_dataset
    from uploads.data_loader import (
        clean_table_name,
        clean_column_name,
        create_table,
        insert_dataframe,
        optimize_dataframe_memory,
        auto_repair_dataframe_headers
    )
    from utils.logger import log_event
except ModuleNotFoundError:
    from connection import get_db_cursor, sanitize_identifier
    from queries import insert_dataset
    from data_loader import (
        clean_table_name,
        clean_column_name,
        create_table,
        insert_dataframe,
        optimize_dataframe_memory,
        auto_repair_dataframe_headers
    )
    try:
        from utils.logger import log_event
    except ModuleNotFoundError:
        def log_event(*args, **kwargs): pass

# 9 Supported File Extensions
SUPPORTED_EXTENSIONS = {
    ".csv": "CSV",
    ".xlsx": "EXCEL",
    ".xls": "EXCEL",
    ".json": "JSON",
    ".xml": "XML",
    ".txt": "TXT",
    ".tsv": "TSV",
    ".parquet": "PARQUET",
    ".feather": "FEATHER"
}

MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB Max Limit


def sanitize_and_clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Auto-detect & fix out-of-format dataset issues:
    - Drop completely empty rows and columns
    - Auto-fix 'Unnamed: X', 'Unnamed__X', or blank column headings
    - Auto-promote row 0 to column headers if >30% columns were unnamed and row 0 has string headers
    - Smart infer human-readable headers (Order_Date, Order_ID, Tracking_ID, Delivery_Status, Amount)
    - Ensure unique, sanitized column names
    """
    if df is None or df.empty:
        return df

    # Drop completely blank rows & columns
    df = df.dropna(how="all").dropna(how="all", axis=1)
    if df.empty:
        return df

    # Run Smart Intelligent Header Auto-Repair & Inferencing
    df = auto_repair_dataframe_headers(df)
    return df


def detect_file_type(filename: str, file_stream: Optional[io.BytesIO] = None) -> Tuple[str, str]:
    """
    Detect file format using extension and magic header verification.
    Returns tuple of (extension, format_label).
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext in SUPPORTED_EXTENSIONS:
        return ext, SUPPORTED_EXTENSIONS[ext]

    # Content signature inspection fallback
    if file_stream:
        file_stream.seek(0)
        head = file_stream.read(1024)
        file_stream.seek(0)

        if head.startswith(b"PAR1"):
            return ".parquet", "PARQUET"
        if head.startswith(b"FEA1"):
            return ".feather", "FEATHER"
        if head.strip().startswith(b"{") or head.strip().startswith(b"["):
            return ".json", "JSON"
        if head.strip().startswith(b"<?xml") or head.strip().startswith(b"<"):
            return ".xml", "XML"

    return "", "UNKNOWN"


def validate_file_pre_upload(uploaded_file, user_id: int) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Perform pre-upload validation:
    - Size check (up to 500 MB)
    - Duplicate filename check per user account
    - File format detection
    - Corruption check
    """
    if uploaded_file is None or not getattr(uploaded_file, "filename", None):
        return False, "No file selected.", {}

    filename = uploaded_file.filename
    ext, format_label = detect_file_type(filename, getattr(uploaded_file, "stream", None))
    if not format_label or format_label == "UNKNOWN":
        return False, f"Unsupported file format '{filename}'. Supported formats: CSV, Excel (.xlsx, .xls), JSON, XML, TXT, TSV, Parquet, Feather.", {}

    # Size verification
    uploaded_file.seek(0, os.SEEK_END)
    file_length = uploaded_file.tell()
    uploaded_file.seek(0)

    if file_length == 0:
        return False, f"File '{filename}' is empty (0 bytes).", {}

    if file_length > MAX_FILE_SIZE_BYTES:
        return False, f"File '{filename}' exceeds maximum allowed size of 500 MB ({round(file_length / (1024*1024), 2)} MB).", {}

    # Duplicate dataset name check (case-insensitive for filename & table_name)
    table_name = clean_table_name(filename, user_id=user_id)
    base_name = filename.rsplit(".", 1)[0]
    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT OriginalFileName FROM Datasets 
                WHERE UserID = ? AND (
                    LOWER(OriginalFileName) = LOWER(?) 
                    OR LOWER(DatasetName) = LOWER(?)
                    OR LOWER(OriginalFileName) = LOWER(?)
                )
                """,
                (user_id, filename, table_name, base_name)
            )
            existing = cursor.fetchone()
            if existing:
                return False, f"Kindly note that a dataset with the name '{filename}' already exists in your account. Please rename your file or choose a different dataset name because a dataset with this name is already uploaded.", {}
    except Exception as err:
        log_event("UPLOAD_LOG", f"Duplicate check notice: {err}", user_id=user_id)

    meta = {
        "filename": filename,
        "extension": ext,
        "format_label": format_label,
        "size_bytes": file_length,
        "storage_size_kb": round(file_length / 1024, 2),
        "table_name": table_name
    }
    return True, "Validation successful.", meta


def parse_uploaded_file(uploaded_file, ext: str, format_label: str) -> pd.DataFrame:
    """
    Parse uploaded file into a Pandas DataFrame according to its format type with encoding auto-recovery.
    """
    uploaded_file.seek(0)

    try:
        if format_label == "CSV":
            try:
                df = pd.read_csv(uploaded_file, encoding="utf-8", low_memory=False, engine="c", on_bad_lines="skip")
            except (UnicodeDecodeError, Exception):
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, encoding="latin1", low_memory=False, engine="c", on_bad_lines="skip")

        elif format_label == "TSV":
            try:
                df = pd.read_csv(uploaded_file, sep="\t", encoding="utf-8", low_memory=False, engine="c", on_bad_lines="skip")
            except (UnicodeDecodeError, Exception):
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, sep="\t", encoding="latin1", low_memory=False, engine="c", on_bad_lines="skip")

        elif format_label == "TXT":
            uploaded_file.seek(0)
            sample = uploaded_file.read(4096)
            uploaded_file.seek(0)
            delim = "\t" if b"\t" in sample else ("," if b"," in sample else r"\s+")
            try:
                df = pd.read_csv(uploaded_file, sep=delim, encoding="utf-8", engine="python")
            except Exception:
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, sep=delim, encoding="latin1", engine="python")

        elif format_label == "EXCEL":
            df = pd.read_excel(uploaded_file)

        elif format_label == "JSON":
            try:
                df = pd.read_json(uploaded_file)
            except Exception:
                uploaded_file.seek(0)
                content = json.load(uploaded_file)
                if isinstance(content, dict):
                    # Check for nested list of records
                    for k, v in content.items():
                        if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                            df = pd.DataFrame(v)
                            break
                    else:
                        df = pd.DataFrame([content])
                elif isinstance(content, list):
                    df = pd.DataFrame(content)
                else:
                    raise ValueError("JSON structure could not be normalized into table records.")

        elif format_label == "XML":
            df = pd.read_xml(uploaded_file)

        elif format_label == "PARQUET":
            df = pd.read_parquet(uploaded_file)

        elif format_label == "FEATHER":
            df = pd.read_feather(uploaded_file)

        else:
            raise ValueError(f"Unsupported format: {format_label}")

        return df

    except Exception as e:
        raise ValueError(f"Failed to parse {format_label} file '{getattr(uploaded_file, 'filename', 'data')}': {str(e)}")


def process_file_upload(uploaded_file, user_id: int = 1, tags: str = None) -> Tuple[bool, Any]:
    """
    Complete end-to-end multi-format file ingestion pipeline:
    1. Pre-upload validation
    2. Parsing file into DataFrame
    3. Cleaning blank rows & duplicate columns
    4. Memory optimization
    5. Database table creation & batch insertion
    6. Storing dataset metadata
    """
    valid, msg, meta = validate_file_pre_upload(uploaded_file, user_id=user_id)
    if not valid:
        return False, msg

    filename = meta["filename"]
    ext = meta["extension"]
    format_label = meta["format_label"]
    table_name = meta["table_name"]
    storage_size_kb = meta["storage_size_kb"]

    try:
        df = parse_uploaded_file(uploaded_file, ext, format_label)

        if df is None or df.empty:
            return False, f"File '{filename}' contains no rows or data records."

        # Sanitize headers, clean blank rows/cols, fix unnamed columns
        df = sanitize_and_clean_dataframe(df)

        if df is None or df.empty:
            return False, f"File '{filename}' contains only blank rows or invalid data."

        # Memory optimization for large datasets
        df = optimize_dataframe_memory(df)

        total_rows = int(df.shape[0])
        total_cols = int(df.shape[1])

        # Create SQL Table
        create_table(table_name, df)

        # Batch Insert Data
        insert_dataframe(table_name, df)

        # Record dataset metadata
        with get_db_cursor(commit=True) as cursor:
            try:
                cursor.execute(
                    insert_dataset(),
                    (
                        user_id,
                        table_name,
                        filename,
                        format_label,
                        total_rows,
                        total_cols,
                        storage_size_kb,
                        tags
                    )
                )
            except Exception:
                fallback_insert = """
                INSERT INTO Datasets (UserID, DatasetName, OriginalFileName, FileType, TotalRows, TotalColumns, StorageSizeKB)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """
                cursor.execute(
                    fallback_insert,
                    (user_id, table_name, filename, format_label, total_rows, total_cols, storage_size_kb)
                )

        log_event("UPLOAD_LOG", f"Successfully ingested {format_label} file '{filename}' into table [{table_name}] ({total_rows} rows, {total_cols} cols)", user_id=user_id)

        preview_html = df.head(10).to_html(
            classes="table table-bordered table-striped custom-table",
            index=False
        )

        return True, {
            "table_name": table_name,
            "file_name": filename,
            "file_type": format_label,
            "rows": total_rows,
            "columns": total_cols,
            "storage_kb": storage_size_kb,
            "preview": preview_html
        }

    except Exception as e:
        log_event("UPLOAD_LOG", f"Failed to ingest file '{filename}': {str(e)}", user_id=user_id)
        return False, f"Upload Error for '{filename}': {str(e)}"
