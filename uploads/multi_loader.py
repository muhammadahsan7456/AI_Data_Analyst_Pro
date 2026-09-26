import os
import sys
import io
import json
import pandas as pd
from typing import Tuple, Dict, Any, Optional

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.connection import get_db_cursor
from database.queries import insert_dataset
from uploads.data_loader import (
    clean_table_name,
    create_table,
    insert_dataframe,
    create_high_performance_indexes,
    optimize_dataframe_memory,
    auto_repair_dataframe_headers
)
from utils.logger import log_event

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

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))
MAX_FILE_SIZE_BYTES = MAX_UPLOAD_MB * 1024 * 1024  # Configurable MB Limit

# Files at or above this size are ingested via the streaming path below instead of being
# fully loaded into one in-memory DataFrame.
STREAMING_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50 MB
STREAMING_SAMPLE_ROWS = 20000
STREAMING_CHUNK_SIZE = 50000


def process_large_delimited_file_streaming(uploaded_file, sep: str, table_name: str) -> Optional[Dict[str, Any]]:
    """
    Stream-parse and stream-insert a large CSV/TSV file in bounded chunks instead of loading
    the entire file into one DataFrame, so peak memory stays roughly constant regardless of
    file size. Column names are inferred once from a sample (the same heuristic the normal
    path already uses) and reused for every subsequent chunk. Unlike the normal path, this
    does NOT drop columns that look empty in the sample - a column empty in the first 20,000
    rows may still contain real data further into the file, and dropping it there would
    silently lose that data.
    """
    uploaded_file.seek(0)
    try:
        sample_df = pd.read_csv(uploaded_file, sep=sep, encoding="utf-8", engine="c",
                                 on_bad_lines="skip", low_memory=False, nrows=STREAMING_SAMPLE_ROWS)
        encoding_used = "utf-8"
    except (UnicodeDecodeError, Exception):
        uploaded_file.seek(0)
        sample_df = pd.read_csv(uploaded_file, sep=sep, encoding="latin1", engine="c",
                                 on_bad_lines="skip", low_memory=False, nrows=STREAMING_SAMPLE_ROWS)
        encoding_used = "latin1"

    if sample_df is None or sample_df.empty:
        return None

    sample_df = sample_df.dropna(how="all")
    if sample_df.empty:
        return None

    sample_df = auto_repair_dataframe_headers(sample_df)
    sample_df = optimize_dataframe_memory(sample_df)
    final_columns = list(sample_df.columns)
    n_cols = len(final_columns)

    create_table(table_name, sample_df.copy())

    preview_df = sample_df.head(500).copy()
    first_result = insert_dataframe(table_name, sample_df)

    total_parsed = len(sample_df)
    total_inserted = first_result["inserted"]
    total_failed = first_result["failed"]

    uploaded_file.seek(0)
    reader = pd.read_csv(
        uploaded_file, sep=sep, encoding=encoding_used, engine="c",
        on_bad_lines="skip", low_memory=False,
        skiprows=range(1, STREAMING_SAMPLE_ROWS + 1), chunksize=STREAMING_CHUNK_SIZE
    )
    for chunk in reader:
        chunk = chunk.dropna(how="all")
        if chunk.empty:
            continue
        if len(chunk.columns) != n_cols:
            chunk = chunk.iloc[:, :n_cols]
        chunk.columns = final_columns
        chunk = optimize_dataframe_memory(chunk)

        result = insert_dataframe(table_name, chunk)
        total_parsed += len(chunk)
        total_inserted += result["inserted"]
        total_failed += result["failed"]
        del chunk

    return {
        "parsed_rows": total_parsed,
        "inserted_rows": total_inserted,
        "failed_rows": total_failed,
        "total_cols": n_cols,
        "preview_df": preview_df
    }


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

    # User-isolated table name generation & smooth duplicate auto-versioning
    base_name = filename.rsplit(".", 1)[0] if "." in filename else filename
    file_ext_part = f".{ext.strip('.')}" if ext else ""
    table_name = clean_table_name(filename, user_id=user_id)
    final_filename = filename

    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                SELECT OriginalFileName, DatasetName FROM Datasets 
                WHERE UserID = ?
                """,
                (user_id,)
            )
            rows = cursor.fetchall() or []
            existing_files = {str(r[0]).lower() for r in rows if r[0]}
            existing_tables = {str(r[1]).lower() for r in rows if r[1]}

            version = 1
            while final_filename.lower() in existing_files or table_name.lower() in existing_tables:
                version += 1
                final_filename = f"{base_name} ({version}){file_ext_part}"
                table_name = clean_table_name(final_filename, user_id=user_id)
    except Exception as err:
        log_event("UPLOAD_LOG", f"Duplicate check notice: {err}", user_id=user_id)

    meta = {
        "filename": final_filename,
        "original_filename": filename,
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
    size_bytes = meta.get("size_bytes", 0)

    try:
        use_streaming = format_label in ("CSV", "TSV") and size_bytes >= STREAMING_THRESHOLD_BYTES

        if use_streaming:
            # Large delimited file: parse and insert in bounded chunks instead of loading
            # the whole file into one DataFrame (keeps peak memory roughly constant).
            sep = "\t" if format_label == "TSV" else ","
            stream_result = process_large_delimited_file_streaming(uploaded_file, sep, table_name)
            if not stream_result:
                return False, f"File '{filename}' contains no rows or data records."

            parsed_rows = stream_result["parsed_rows"]
            total_cols = stream_result["total_cols"]
            total_rows = stream_result["inserted_rows"]
            failed_rows = stream_result["failed_rows"]
            df = stream_result["preview_df"]

            log_event("UPLOAD_LOG", f"Streamed large {format_label} file '{filename}' ({round(size_bytes/1024/1024, 1)} MB) into table [{table_name}]", user_id=user_id)
        else:
            df = parse_uploaded_file(uploaded_file, ext, format_label)

            if df is None or df.empty:
                return False, f"File '{filename}' contains no rows or data records."

            # Sanitize headers, clean blank rows/cols, fix unnamed columns
            df = sanitize_and_clean_dataframe(df)

            if df is None or df.empty:
                return False, f"File '{filename}' contains only blank rows or invalid data."

            # Memory optimization for large datasets
            df = optimize_dataframe_memory(df)

            parsed_rows = int(df.shape[0])
            total_cols = int(df.shape[1])

            # Create SQL Table
            create_table(table_name, df)

            # Batch Insert Data - report the ACTUAL rows committed to the database,
            # not just the rows parsed from the file, since insertion can partially fail.
            insert_result = insert_dataframe(table_name, df)
            total_rows = insert_result["inserted"]
            failed_rows = insert_result["failed"]

        if failed_rows > 0:
            log_event(
                "UPLOAD_LOG",
                f"Dataset '{filename}' -> table [{table_name}]: parsed {parsed_rows} rows, "
                f"inserted {total_rows}, {failed_rows} row(s) failed to insert and were skipped.",
                user_id=user_id,
                level="WARNING"
            )

        # Create non-clustered high performance indexes on search columns
        create_high_performance_indexes(table_name, df)

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

        dataset_id = None
        try:
            with get_db_cursor() as cursor:
                cursor.execute("SELECT DatasetID FROM Datasets WHERE UserID = ? AND DatasetName = ?", (user_id, table_name))
                row = cursor.fetchone()
                if row:
                    dataset_id = row[0]
        except Exception:
            pass

        log_event("UPLOAD_LOG", f"Successfully ingested {format_label} file '{filename}' into table [{table_name}] (parsed {parsed_rows}, inserted {total_rows}, {failed_rows} failed, {total_cols} cols)", user_id=user_id)

        preview_html = df.head(500).to_html(
            classes="table table-bordered table-striped custom-table upload-preview-table",
            index=False
        )

        return True, {
            "dataset_id": dataset_id,
            "table_name": table_name,
            "file_name": filename,
            "file_type": format_label,
            "rows": total_rows,
            "parsed_rows": parsed_rows,
            "failed_rows": failed_rows,
            "columns": total_cols,
            "storage_kb": storage_size_kb,
            "preview": preview_html
        }

    except Exception as e:
        log_event("UPLOAD_LOG", f"Failed to ingest file '{filename}': {str(e)}", user_id=user_id)
        return False, f"Upload Error for '{filename}': {str(e)}"
