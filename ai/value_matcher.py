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
import time
import difflib
from database.connection import run_query, sanitize_identifier

try:
    from utils.cache import system_cache
except Exception:  # pragma: no cover - cache is an optimisation, not a requirement
    system_cache = None

# A column with more distinct values than this is free text or an identifier, not a
# category worth matching question words against. One over the limit is enough to tell.
MAX_DISTINCT_FOR_VOCAB = 500

# `SELECT DISTINCT TOP n` against the FULL table used to be the query here, on the
# reasoning that TOP would let the engine stop early. It doesn't: on an ad-hoc uploaded
# table (a heap, no supporting index on these columns) SQL Server has to scan and
# hash/sort every matching row before it can hand back the first N distinct ones, so this
# scaled with total table size, not with the column's actual cardinality. Measured on a
# real 1,000,000-row table: a single categorical column's full-table DISTINCT took 6-16
# seconds, and the six categorical columns a typical dataset has would sum to a request
# that could run past a minute with no feedback - exactly the "chat hangs forever" defect.
#
# The columns this ever runs against are already known to be low-cardinality (schema
# intelligence only classifies a column as categorical when a 300-row sample found at
# most ~100 distinct values), so a bounded sample from the table is enough to see the
# same handful of values again and again - it does not need to touch every row to find
# them all. Measured on the same 1,000,000-row table, sampling 20,000 rows per column
# found the identical distinct values in 2 seconds total for six columns, versus the
# same six columns taking well over a minute against the full table.
VOCAB_SAMPLE_ROWS = 20_000

# Hard ceiling on the WHOLE vocabulary build across every candidate column, regardless of
# how slow any individual query turns out to be on a given machine/table. If this is hit,
# whatever vocabulary was gathered so far is used (and cached) rather than the request
# hanging indefinitely waiting for every last column.
VOCAB_BUILD_TIME_BUDGET_SECONDS = 8.0

# Words that appear in ordinary questions and would otherwise match a data value by
# accident (a "Total" category, a status literally called "All", and so on).
STOPWORDS = {
    "all", "and", "any", "are", "avg", "but", "can", "count", "data", "date", "day",
    "for", "from", "get", "give", "has", "have", "how", "in", "is", "list", "many",
    "max", "me", "mean", "min", "month", "most", "much", "no", "not", "number", "of",
    "on", "or", "per", "show", "some", "sum", "than", "that", "the", "them", "there",
    "this", "to", "top", "total", "value", "was", "week", "were", "what", "when",
    "where", "which", "who", "why", "with", "year", "yes",
    # Roman Urdu connectives and data terms the app's users type constantly
    "aur", "bhi", "batao", "dikhao", "kitne", "kitni", "kya", "sab", "sara", "sare",
    "mein", "ka", "ke", "ki", "ko", "hai", "hain", "wala", "wali", "unka", "unke",
    "inke", "iska", "uske", "mujhe", "mujha", "hum", "humein", "aaye", "gaye",
    "raha", "rahe", "rahi", "la", "do", "den", "karo", "karein", "records", "record",
    "orders", "order", "data", "rows", "row", "number", "count", "btao", "dekho",
    "han", "haan", "acha", "sirf", "bas", "kaun", "kaha", "kahan", "kab", "kyun",
}


def _vocab_cache_key(table_name: str) -> str:
    return f"colvocab_{sanitize_identifier(table_name)}"


def get_column_vocabulary(table_name: str, schema_intel: dict = None, use_cache: bool = True) -> dict:
    """
    Return {column_name: [distinct values]} for every low-cardinality text column.

    Values come from a bounded SAMPLE of the table (see VOCAB_SAMPLE_ROWS above), not the
    whole column - only low-cardinality columns ever reach this function, so a value real
    enough to filter on shows up dozens or hundreds of times per million rows and a
    20,000-row sample finds it with overwhelming probability. This is what keeps a
    million-row table's first query fast instead of one full-table DISTINCT scan per
    candidate column.
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
    build_start = time.time()
    for col in candidates:
        if time.time() - build_start > VOCAB_BUILD_TIME_BUDGET_SECONDS:
            # Whatever hasn't been scanned yet is skipped for this pass rather than
            # letting one slow table hold the whole chat request open indefinitely - a
            # partial vocabulary (still cached below) beats an unbounded wait.
            break
        try:
            safe_col = sanitize_identifier(col)
            # A per-query timeout as a second, independent safety net beneath the sample
            # bound above: if this specific table/column is unexpectedly slow for a reason
            # the sample size doesn't protect against, it fails fast and is simply skipped
            # (still eligible on the next uncached call) instead of the request hanging.
            df = run_query(
                f"SELECT DISTINCT TOP {MAX_DISTINCT_FOR_VOCAB + 1} V FROM ("
                f"SELECT TOP {VOCAB_SAMPLE_ROWS} {safe_col} AS V FROM {safe_tbl} "
                f"WHERE {safe_col} IS NOT NULL) AS sampled;",
                timeout=5,
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


# City code lookup for Pakistani and regional logistics datasets
_CITY_CODE_MAP = {
    "KHI": ["Karachi", "Krachi"],
    "LHE": ["Lahore", "Lahaur"],
    "LHR": ["Lahore", "Lahaur"],
    "ISB": ["Islamabad", "Islamabd"],
    "RWP": ["Rawalpindi", "Pindi"],
    "MUX": ["Multan"],
    "PEW": ["Peshawar"],
    "FSD": ["Faisalabad", "Faislabad"],
    "UET": ["Quetta"],
    "HDD": ["Hyderabad"],
    "SKZ": ["Sukkur"],
    "BWP": ["Bahawalpur"],
    "BWL": ["Bahawalpur"],
    "NWS": ["Nawabshah"],
    "TLG": ["Talagang"],
    "NOW": ["Nowshera"],
    "KOT": ["Kotli"],
    "DRG": ["Dera Ghazi Khan"],
    "JHG": ["Jhang"],
    "DNR": ["Dina"],
    "GJR": ["Gujranwala"],
    "SKT": ["Sialkot"],
    "GUJ": ["Gujrat"],
    "SWL": ["Sahiwal"],
    "MIR": ["Mirpur"],
    "ABT": ["Abbottabad"]
}


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

    v_upper = v.upper()
    if v_upper in _CITY_CODE_MAP:
        out.extend(_CITY_CODE_MAP[v_upper])
    else:
        v_low = v.lower()
        for code, names in _CITY_CODE_MAP.items():
            if any(n.lower() == v_low for n in names):
                out.append(code)

    # Status value aliases & stemming
    v_low = v.lower()
    if "return" in v_low:
        out.extend(["return", "returns", "returned", "rto", "returnd"])
    elif "deliver" in v_low:
        out.extend(["deliver", "delivers", "delivered", "delivery", "deliverd"])
    elif "cancel" in v_low:
        out.extend(["cancel", "cancels", "cancelled", "canceled"])
    elif "refund" in v_low:
        out.extend(["refund", "refunds", "refunded"])
    elif "pend" in v_low:
        out.extend(["pending", "pend"])

    seen, uniq = set(), []
    for a in out:
        if a and a.lower() not in seen:
            seen.add(a.lower())
            uniq.append(a)
    return uniq


def find_values_in_question(question: str, vocab: dict) -> dict:
    """
    Return {column: [every value of that column mentioned in the question]}.
    Disambiguates between columns sharing the same value (e.g. Origin vs Destination,
    Delivery_Status vs Status_Description) by ranking column affinity and preventing
    overlapping text spans from being claimed multiple times.
    """
    if not question or not vocab:
        return {}

    q_low = question.lower()

    # Step 1: Collect candidate matches across all columns
    candidates = []
    for col, values in vocab.items():
        col_l = col.lower()
        alias_map = {}
        for val in values:
            for alias in _aliases_for(val):
                if _is_matchable(alias):
                    alias_map.setdefault(alias.lower(), (alias, val))

        for alias_low in sorted(alias_map, key=len, reverse=True):
            _, original = alias_map[alias_low]
            for m in re.finditer(r"(?<![\w])" + re.escape(alias_low) + r"(?![\w])", q_low):
                span = (m.start(), m.end())
                score = len(alias_low) * 10
                # Column name explicitly mentioned in question
                if col_l in q_low or col_l.replace("_", " ") in q_low:
                    score += 50
                # Destination is prioritized over Origin unless origin keywords are explicit
                if col_l in ("destination", "destination_city", "dest_city", "city", "customer_city"):
                    score += 20 if not any(kw in q_low for kw in ["origin", "from", "se rawana"]) else 0
                elif col_l in ("origin", "origin_city"):
                    score += 30 if any(kw in q_low for kw in ["origin", "from", "se rawana"]) else -10
                # Primary status column prioritized over verbose description column
                if col_l in ("delivery_status", "status", "order_status"):
                    score += 25
                elif col_l in ("status_description", "description", "remarks"):
                    score += -15

                candidates.append({
                    "span": span,
                    "col": col,
                    "val": original,
                    "score": score,
                    "len": len(alias_low)
                })

    # Sort candidates by score descending, then length descending
    candidates.sort(key=lambda x: (x["score"], x["len"]), reverse=True)

    claimed_spans = []
    found = {}
    for cand in candidates:
        span = cand["span"]
        # If this span overlaps an already claimed higher-priority span, skip
        if any(span[0] < c[1] and c[0] < span[1] for c in claimed_spans):
            continue
        claimed_spans.append(span)
        col = cand["col"]
        if col not in found:
            found[col] = []
        if cand["val"] not in found[col]:
            found[col].append(cand["val"])

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
# sentence or are common English/Urdu question words - never a data value, so they must not
# be flagged as "the user named a value we don't have".
_PROPER_NOUN_STOPWORDS = {
    "how", "what", "which", "who", "when", "where", "why", "show", "give", "list",
    "display", "compare", "get", "find", "tell", "is", "are", "does", "do", "can",
    "i", "my", "the", "a", "an", "in", "for", "of", "and", "or", "with", "please",
    "top", "first", "last", "total", "average", "count", "all", "more", "less",
    # Roman Urdu question starters, pronouns and verbs
    "mujhe", "mujha", "hum", "humein", "humko", "unka", "unke", "unki", "inka", "inke", "inki",
    "iska", "iske", "uski", "uske", "mera", "meri", "mere", "aap", "tum", "yeh", "ye", "woh", "wo",
    "aaye", "aayein", "gaye", "hua", "hue", "hui", "raha", "rahe", "rahi", "tha", "the", "thi",
    "la", "lao", "do", "den", "karo", "karein", "karna", "batao", "bataen", "batayein", "batado",
    "dikhao", "dikhayein", "btao", "dekho", "kitna", "kitne", "kitni", "kaun", "koun", "kaha",
    "kahan", "kab", "kyun", "kyu", "kese", "kaise", "se", "me", "mein", "par", "pe", "ko",
    "ka", "ki", "ke", "hai", "hain", "han", "haan", "acha", "achha", "sirf", "bas",
    # Generic dataset analytical nouns
    "record", "records", "data", "rows", "row", "number", "orders", "order", "items", "item",
    "table", "details", "info", "breakdown", "summary", "stats", "statistics", "rate", "audit", "audits",
    "delivery", "deliveries", "delivered", "return", "returns", "returned", "status",
    "destination", "origin", "customer", "customers", "client", "clients", "city", "cities",
    "country", "countries", "state", "states", "category", "categories", "product", "products",
    # Month names
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}


def find_unresolved_proper_nouns(question: str, table_name: str, schema_intel: dict = None) -> list:
    """
    Find capitalised, name-like words in the question that do not match any known value
    in any categorical column of this dataset.
    """
    if not question:
        return []

    vocab = get_column_vocabulary(table_name, schema_intel)
    all_known_values = set()
    for values in vocab.values():
        for v in values:
            for alias in _aliases_for(v):
                all_known_values.add(alias.lower())

    # Preload city names and abbreviations
    for code, names in _CITY_CODE_MAP.items():
        all_known_values.add(code.lower())
        for n in names:
            all_known_values.add(n.lower())

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
        if raw.lower() in all_known_values or raw.lower() in column_names:
            i += 1
            continue

        # Greedily absorb a run of consecutive capitalised words as one phrase, e.g. "New York"
        j = i + 1
        phrase_words = [raw]
        while j < len(words):
            nxt_raw = re.sub(r"^[^\w]+|[^\w]+$", "", words[j])
            nxt_core = re.sub(r"[^A-Za-z]", "", nxt_raw)
            if len(nxt_core) >= 2 and nxt_core[:1].isupper() and nxt_core.lower() not in _PROPER_NOUN_STOPWORDS:
                # If next word is individually already a known city or column, stop grouping
                if nxt_raw.lower() in all_known_values or nxt_raw.lower() in column_names:
                    break
                phrase_words.append(nxt_raw)
                j += 1
            else:
                break

        phrase = " ".join(phrase_words)
        if (phrase.lower() not in all_known_values
                and raw.lower() not in all_known_values
                and phrase.lower() not in column_names
                and raw.lower() not in column_names
                and not any(pw.lower() in all_known_values or pw.lower() in column_names or pw.lower() in _PROPER_NOUN_STOPWORDS for pw in phrase_words)):
            unresolved.append(phrase)
        i = j if j > i + 1 else i + 1

    return unresolved
