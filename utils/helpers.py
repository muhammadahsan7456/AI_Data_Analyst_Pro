import re
import time
from contextlib import contextmanager


def is_safe_select_query(query: str) -> bool:
    """
    Ensure SQL query strictly performs SELECT operations and contains no destructive DDL/DML.
    """
    if not query or not isinstance(query, str):
        return False

    trimmed = query.strip()
    if not re.match(r"^\s*SELECT\b", trimmed, re.IGNORECASE):
        return False

    forbidden_keywords = [
        r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bDROP\b",
        r"\bALTER\b", r"\bTRUNCATE\b", r"\bEXEC\b", r"\bEXECUTE\b",
        r"\bCREATE\b", r"\bGRANT\b", r"\bREVOKE\b"
    ]

    for kw in forbidden_keywords:
        if re.search(kw, trimmed, re.IGNORECASE):
            return False

    return True


def format_bytes(bytes_count: float) -> str:
    """
    Format byte size into human readable string (KB, MB, GB).
    """
    if bytes_count < 1024:
        return f"{bytes_count:.2f} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.2f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.2f} MB"
    else:
        return f"{bytes_count / (1024 * 1024 * 1024):.2f} GB"


def format_12hr_datetime(val) -> str:
    """
    Format ISO / SQL timestamp into 12-Hour AM/PM format (e.g. 'Aug 16, 2026, 02:30 PM').
    """
    if not val:
        return ""
    val_str = str(val).strip()
    if not val_str:
        return ""

    try:
        from datetime import datetime
        if isinstance(val, datetime):
            return val.strftime("%b %d, %Y, %I:%M %p")

        val_clean = val_str.replace("Z", "").split("+")[0]
        if "." in val_clean:
            dt = datetime.strptime(val_clean.split(".")[0], "%Y-%m-%d %H:%M:%S")
        elif "T" in val_clean:
            dt = datetime.strptime(val_clean, "%Y-%m-%dT%H:%M:%S")
        else:
            dt = datetime.strptime(val_clean, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%b %d, %Y, %I:%M %p")
    except Exception:
        try:
            from datetime import datetime
            dt = datetime.strptime(val_str[:10], "%Y-%m-%d")
            return dt.strftime("%b %d, %Y")
        except Exception:
            return val_str


@contextmanager
def timer():
    """
    Execution timer context manager.
    """
    start = time.perf_counter()
    res = {}
    try:
        yield res
    finally:
        res["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
