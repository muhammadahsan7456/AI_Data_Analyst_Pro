import re
import os
import sys
from typing import Optional, List, Dict, Any

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from database.connection import get_db_cursor, get_table_columns
    from database.queries import get_all_datasets
except ModuleNotFoundError:
    from connection import get_db_cursor, get_table_columns
    from queries import get_all_datasets


def detect_best_dataset_for_query(question: str, user_id: int) -> Optional[Dict[str, Any]]:
    """
    Smart Dataset Selection Engine (Feature 6).
    Scans user's uploaded datasets and their column schemas. Matches keywords in user's question
    against table names, column names, and dataset metadata to automatically select the target dataset.
    """
    if not question or not isinstance(question, str):
        return None

    q_lower = question.lower().strip()
    q_words = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", q_lower))

    with get_db_cursor() as cursor:
        try:
            cursor.execute(get_all_datasets(user_id=user_id))
            datasets = cursor.fetchall()
        except Exception:
            return None

    if not datasets:
        return None

    # Score each dataset based on column & filename relevance
    best_score = -1
    best_dataset = None

    for ds in datasets:
        # ds structure: (DatasetID, DatasetName, OriginalFileName, FileType, TotalRows, TotalColumns, StorageSizeKB, IsFavorite, Tags, LastOpenedAt, UploadDate)
        dataset_id = ds[0]
        table_name = ds[1]
        orig_filename = ds[2].lower() if len(ds) > 2 and ds[2] else ""
        tags = ds[8].lower() if len(ds) > 8 and ds[8] else ""

        score = 0

        # Match table name or filename words
        clean_file_words = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", orig_filename))
        if q_words.intersection(clean_file_words):
            score += 10

        # Match tags
        if tags:
            tag_words = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", tags))
            if q_words.intersection(tag_words):
                score += 5

        # Fetch columns and match against query words
        cols = get_table_columns(table_name)
        col_names_lower = [str(c).lower() for c in cols]
        
        for col in col_names_lower:
            if col in q_words:
                score += 8
            else:
                for q_word in q_words:
                    if len(q_word) >= 3 and q_word in col:
                        score += 3

        if score > best_score:
            best_score = score
            best_dataset = {
                "dataset_id": dataset_id,
                "table_name": table_name,
                "original_filename": ds[2],
                "columns": cols,
                "match_score": score
            }

    # If score > 0, return matched dataset; otherwise default to most recently uploaded
    if best_dataset and best_dataset["match_score"] > 0:
        return best_dataset

    # Fallback to latest dataset
    latest_ds = datasets[0]
    return {
        "dataset_id": latest_ds[0],
        "table_name": latest_ds[1],
        "original_filename": latest_ds[2],
        "columns": get_table_columns(latest_ds[1]),
        "match_score": 0
    }
