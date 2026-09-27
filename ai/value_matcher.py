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
import difflib
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

        # A non-technical user's typo ("laptp" for "Laptop") should still be recognised -
        # only tried when the exact pass found nothing at all for this column, and only
        # against single question words that overlap no already-claimed span, so it can
        # never override a real exact match elsewhere in the sentence.
        if not hits:
            q_words = re.findall(r"[a-z0-9]+", q_low)
            for w in q_words:
                if len(w) < 4:
                    continue
                close = difflib.get_close_matches(w, list(alias_map.keys()), n=1, cutoff=0.8)
                if close:
                    _, original = alias_map[close[0]]
                    if original not in hits:
                        hits.append(original)

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


# Sentence-starter / generic words that are capitalised only because they open the
# sentence or are common English question words - never a data value, so they must not
# be flagged as "the user named a value we don't have".
_PROPER_NOUN_STOPWORDS = {
    "how", "what", "which", "who", "when", "where", "why", "show", "give", "list",
    "display", "compare", "get", "find", "tell", "is", "are", "does", "do", "can",
    "i", "my", "the", "a", "an", "in", "for", "of", "and", "or", "with", "please",
    "top", "first", "last", "total", "average", "count", "all",
    # Month names are a date-range reference already handled by the query planner's own
    # date parsing, not a value the user typed for some column - "March" in "revenue in
    # March 2025" is not a missing city/product/etc.
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}


def find_unresolved_proper_nouns(question: str, table_name: str, schema_intel: dict = None) -> list:
    """
    Find capitalised, name-like words in the question that do not match any known value
    in any categorical column of this dataset - the "Sialkot" problem: a city name that
    isn't recognised should be reported as not found, not silently dropped so the query
    runs unfiltered and returns a count of everything as if the word was never there.

    This is necessarily heuristic (matching capitalisation, not semantics), so it only
    flags words that look like proper nouns and are not common English/question-starter
    words, not already a recognised value, and not the name of a real column.
    """
    if not question:
        return []

    vocab = get_column_vocabulary(table_name, schema_intel)
    all_known_values = set()
    for values in vocab.values():
        for v in values:
            for alias in _aliases_for(v):
                all_known_values.add(alias.lower())

    column_names = {c.lower() for c in (schema_intel or {}).get("column_list", [])}

    words = question.split()
    unresolved = []
    i = 0
    while i < len(words):
        raw = re.sub(r"^[^\w]+|[^\w]+$", "", words[i])  # strip surrounding punctuation only
        core = re.sub(r"[^A-Za-z]", "", raw)
        if len(core) < 3 or not core[:1].isupper() or core.lower() in _PROPER_NOUN_STOPWORDS:
            i += 1
            continue
        # A code-like token (letters + digits/underscore, e.g. "Rep_007") is checked whole
        # first - stripping it down to letters-only would turn a real, matched value like
        # "Rep_007" into the meaningless fragment "Rep" and flag it as unresolved.
        if raw.lower() in all_known_values or raw.lower() in column_names:
            i += 1
            continue

        # Greedily absorb a run of consecutive capitalised words as one phrase, e.g. "New
        # York" - checked as a whole before falling back to the single word.
        j = i + 1
        phrase_words = [raw]
        while j < len(words):
            nxt_raw = re.sub(r"^[^\w]+|[^\w]+$", "", words[j])
            nxt_core = re.sub(r"[^A-Za-z]", "", nxt_raw)
            if len(nxt_core) >= 2 and nxt_core[:1].isupper() and nxt_core.lower() not in _PROPER_NOUN_STOPWORDS:
                phrase_words.append(nxt_raw)
                j += 1
            else:
                break
        phrase = " ".join(phrase_words)
        if (phrase.lower() not in all_known_values
                and raw.lower() not in all_known_values
                and phrase.lower() not in column_names
                and raw.lower() not in column_names):
            unresolved.append(phrase)
        i = j if j > i + 1 else i + 1

    return unresolved
