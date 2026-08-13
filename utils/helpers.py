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
