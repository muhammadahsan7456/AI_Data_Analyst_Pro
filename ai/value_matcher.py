"""
AI Data Analyst Pro - Field-agnostic multi-value filter extraction.

The query planner used to recognise filter values from hardcoded vocabularies: a list of
20 country names, a list of 8 Pakistani cities, and an if/elif chain of status keywords.
Three consequences followed, and all three were bugs:

  * only ONE value was ever taken per field - the country loop `break`s on its first hit,
    the status chain is if/elif, and the generic fallback `break`s after one sample value,
    so "Karachi and Lahore" or "pending and cancelled" silently answered half the question;
  * any value outside those lists was invisible - a product name, a sales rep, a channel,
    a category, a city not on the list;
  * the generic fallback only ran when nothing else had matched at all.

This module replaces all of that with one mechanism that learns each column's vocabulary
from the dataset itself, so it works for whatever columns and values a user happens to
have uploaded, without anybody adding them to a list. Every value the question mentions
for a given column is collected and emitted as a single `[col] IN (?, ?, ...)` filter.
"""

import re
from database.connection import run_query, sanitize_identifier

try:
    from utils.cache import system_cache
except Exception:  # pragma: no cover - cache is an optimisation, not a requirement
    system_cache = None

# A column with more distinct values than this is free text or an identifier, not a
# category worth matching question words against. One over the limit is enough to tell.
MAX_DISTINCT_FOR_VOCAB = 500

# Words that appear in ordinary questions and would otherwise match a data value by
# accident (a "Total" category, a status literally called "All", and so on).
STOPWORDS = {
    "all", "and", "any", "are", "avg", "but", "can", "count", "data", "date", "day",
    "for", "from", "get", "give", "has", "have", "how", "in", "is", "list", "many",
    "max", "me", "mean", "min", "month", "most", "much", "no", "not", "number", "of",
    "on", "or", "per", "show", "some", "sum", "than", "that", "the", "them", "there",
    "this", "to", "top", "total", "value", "was", "week", "were", "what", "when",
    "where", "which", "who", "why", "with", "year", "yes",
    # Roman Urdu connectives the app's users type constantly
    "aur", "bhi", "batao", "dikhao", "kitne", "kitni", "kya", "sab", "sara", "sare",
    "mein", "ka", "ke", "ki", "ko", "hai", "hain", "wala", "wali",
}


def _vocab_cache_key(table_name: str) -> str:
    return f"colvocab_{sanitize_identifier(table_name)}"


def get_column_vocabulary(table_name: str, schema_intel: dict = None, use_cache: bool = True) -> dict:
    """
    Return {column_name: [distinct values]} for every low-cardinality text column.

    Values come from the whole column, not from a sample: a 300-row sample misses values
    that only appear later in the file, and a filter built from it would silently answer
    about the wrong subset. High-cardinality columns are skipped entirely, which is what
    keeps this cheap - one `SELECT DISTINCT TOP n` per candidate column, cached for an
    hour, and the query stops as soon as it has one row more than the limit.
    """
    key = _vocab_cache_key(table_name)
    if use_cache and system_cache:
        cached = system_cache.get(key)
        if cached is not None:
            return cached

    safe_tbl = sanitize_identifier(table_name)
    candidates = []
    if schema_intel:
        # Text and categorical columns only. Numeric and date columns have their own
        # dedicated comparison/range handling in the planner.
        candidates = list(schema_intel.get("text_columns") or [])
        for col in (schema_intel.get("categorical_columns") or {}):
            if col not in candidates:
                candidates.append(col)

    vocab = {}
    for col in candidates:
        try:
            safe_col = sanitize_identifier(col)
            df = run_query(
                f"SELECT DISTINCT TOP {MAX_DISTINCT_FOR_VOCAB + 1} {safe_col} AS V "
                f"FROM {safe_tbl} WHERE {safe_col} IS NOT NULL;"
            )
        except Exception:
            continue
        if df is None or df.empty or len(df) > MAX_DISTINCT_FOR_VOCAB:
            continue
        vals = []
        for v in df["V"].tolist():
            s = str(v).strip()
            if s:
                vals.append(s)
        if vals:
            vocab[col] = vals

    if system_cache:
        system_cache.set(key, vocab, ttl=3600)
    return vocab


def invalidate_column_vocabulary(table_name: str):
    """Drop the cached vocabulary for a table (call after the data changes)."""
    if system_cache:
        try:
            system_cache.delete(_vocab_cache_key(table_name))
        except Exception:
            try:
                system_cache.invalidate_pattern(_vocab_cache_key(table_name))
            except Exception:
                pass


def _is_matchable(value: str) -> bool:
    """Reject values that would match ordinary question words or digits by accident."""
    v = value.strip().lower()
    if len(v) < 2:
        return False
    if v in STOPWORDS:
        return False
    # A bare number would match the "5" in "top 5 records".
    if re.fullmatch(r"[\d\s.,%-]+", v):
        return False
    return True


def _aliases_for(value: str) -> list:
    """
    Surface forms a user might type for a stored value.

    Datasets in this app frequently store a label with a code attached, like
    "Karachi (KHI)" or "Lahore - LHE". Users type just "Karachi", so matching only the
    verbatim stored string would find nothing. Each alias still maps back to the full
    stored value, which is what goes into the SQL, so the filter stays exact.
    """
    v = str(value).strip()
    out = [v]

    # "Karachi (KHI)" -> "Karachi", "KHI"
    m = re.match(r"^(.*?)\s*[\(\[]\s*([^)\]]+?)\s*[\)\]]\s*$", v)
    if m:
        out.extend([m.group(1).strip(), m.group(2).strip()])

    # "Lahore - LHE" / "Lahore / LHE" -> both sides
    for sep in (" - ", " / ", " | ", " – "):
        if sep in v:
            out.extend(p.strip() for p in v.split(sep))

    seen, uniq = set(), []
    for a in out:
        if a and a.lower() not in seen:
            seen.add(a.lower())
            uniq.append(a)
    return uniq


def find_values_in_question(question: str, vocab: dict) -> dict:
    """
    Return {column: [every value of that column mentioned in the question]}.

    Longer surface forms are tested first so that a value containing another one
    ("New York" vs "York") claims its text before the shorter one can match inside it.
    """
    if not question or not vocab:
        return {}

    q_low = question.lower()
    found = {}

    for col, values in vocab.items():
        # alias -> original stored value
        alias_map = {}
        for val in values:
            for alias in _aliases_for(val):
                if _is_matchable(alias):
                    alias_map.setdefault(alias.lower(), (alias, val))

        hits = []
        # Track claimed character spans so "Islamabad" cannot also register a hit for a
        # shorter value like "Islam" sitting inside it.
        claimed = []
        for alias_low in sorted(alias_map, key=len, reverse=True):
            _, original = alias_map[alias_low]
            for m in re.finditer(r"(?<![\w])" + re.escape(alias_low) + r"(?![\w])", q_low):
                span = (m.start(), m.end())
                if any(span[0] < c[1] and c[0] < span[1] for c in claimed):
                    continue
                claimed.append(span)
                if original not in hits:
                    hits.append(original)
                break
        if hits:
            found[col] = hits

    return found


def extract_multi_value_filters(question: str, table_name: str, schema_intel: dict = None) -> list:
    """
    Build one filter per column for EVERY value the question mentions.

    A column with several mentioned values produces `[col] IN (?, ?, ...)` rather than a
    single equality, which is the whole point: asking about two products, three statuses
    or four cities now returns all of them instead of just the first one matched.
    """
    vocab = get_column_vocabulary(table_name, schema_intel)
    found = find_values_in_question(question, vocab)

    filters = []
    for col, values in found.items():
        placeholders = ", ".join("?" for _ in values)
        filters.append({
            "column": col,
            "sql_safe_column": f"[{col}]",
            "operator": "IN" if len(values) > 1 else "=",
            "value": ", ".join(values),
            "values": list(values),
            "sql_clause": (f"[{col}] IN ({placeholders})" if len(values) > 1
                           else f"[{col}] = ?"),
            "params": list(values),
        })
    return filters
