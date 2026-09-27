"""
AI Data Analyst Pro - Schema-driven metric (numeric) and grouping (categorical) column
resolution, plus generic numeric comparison parsing.

Why this exists: the query planner used to decide "which numeric column does 'total X'
mean" and "which categorical column does 'top N Y' mean" using two narrow hardcoded
keyword whitelists - e.g. numeric candidates were only columns whose NAME contained one
of ["cod","val","amount","price","cost","weight","total","pieces","quantity"], and
grouping candidates only columns matching
["destination","origin","status","city","country","company","category","type","state"].

A column like "Revenue" or "SalesRep" matches neither list, so "total revenue" silently
summed whatever OTHER numeric column happened to be first in the table, and "top 3 sales
reps by revenue" silently grouped by the "Revenue" column itself (since it's the only
column name literally present in the question) instead of by SalesRep. Both failures
produce a confident, wrong-looking-plausible number - worse than an error.

This module resolves both kinds of column against the dataset's REAL schema (numeric
columns from actual pandas dtype detection, categorical columns from actual value
cardinality - both already computed by ai/schema_intelligence.py) instead of a fixed list
of English words, so it works for whatever columns a user's dataset happens to have.
"""

import re
import difflib

# Common, dataset-agnostic English words for "the kind of number being asked about".
# These are generic business/analytics vocabulary - not specific data VALUES (those come
# from value_matcher.py) - so keeping this list broad costs nothing and only helps: it is
# consulted only when the column's own name isn't already mentioned in the question.
_METRIC_SYNONYMS = {
    "revenue": ["revenue", "sales", "turnover", "income"],
    "sales": ["sales", "revenue", "turnover"],
    "amount": ["amount", "amt", "value", "cod"],
    "value": ["value", "amount", "cod", "worth"],
    "price": ["price", "rate", "unitprice"],
    "cost": ["cost", "expense", "price"],
    "quantity": ["quantity", "qty", "units", "pieces"],
    "profit": ["profit", "margin", "earning"],
    "margin": ["margin", "profit"],
    "discount": ["discount", "rebate"],
    "salary": ["salary", "wage", "pay", "income"],
    "score": ["score", "rating", "grade"],
    "rating": ["rating", "score"],
    "weight": ["weight", "mass"],
    "duration": ["duration", "hours", "minutes"],
    "balance": ["balance", "due", "outstanding"],
    "tax": ["tax", "vat", "gst"],
    "fee": ["fee", "charge"],
    "spend": ["spend", "spending", "expense", "cost"],
    "budget": ["budget", "allocation"],
}


def is_id_like_column(col: str) -> bool:
    """
    True for a column that identifies a row rather than measuring something about it.

    Deliberately checks the ORIGINAL casing for a capital "ID" suffix (OrderID,
    CustomerID, UserID - the overwhelmingly common convention for identifier columns) so
    it does not also catch ordinary words that merely end in the letters "i" and "d",
    such as "Paid" or "Valid".
    """
    cl = col.lower().strip()
    if cl in ("recordid", "index", "sr", "s.no", "sno", "id", "guid", "uuid", "rowid"):
        return True
    if cl.endswith("_id") or col.endswith("ID"):
        return True
    return False


def get_real_numeric_columns(schema_intel: dict) -> list:
    """The dataset's actual numeric columns (by dtype), minus row identifiers."""
    return [c for c in (schema_intel.get("numeric_columns") or []) if not is_id_like_column(c)]


def get_real_categorical_columns(schema_intel: dict) -> list:
    """The dataset's actual categorical columns (by real value cardinality)."""
    return list((schema_intel.get("categorical_columns") or {}).keys())


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _singularize(s: str) -> str:
    """Cheap English singularisation - just enough to match "cities"->"city",
    "categories"->"category", "statuses"->"status", "channels"->"channel"."""
    if s.endswith("ies") and len(s) > 4:
        return s[:-3] + "y"
    if s.endswith("es") and len(s) > 4 and s[-3] in "sxzh":
        return s[:-2]
    if s.endswith("s") and not s.endswith("ss") and len(s) > 3:
        return s[:-1]
    return s


def resolve_metric_column(q_lower: str, numeric_cols: list):
    """
    Which numeric column is "total X" / "average X" / "X over N" talking about?

    Returns (column_name_or_None, matched_by_exact_name: bool). The exact-name match is
    tried first and always wins over the synonym table, so a dataset that happens to name
    its column "Revenue" is matched on the literal word "revenue" before any synonym logic
    runs at all - the synonym table only helps when the user's word and the column's name
    genuinely differ (e.g. asking for "sales" when the column is named "Revenue").
    """
    if not numeric_cols:
        return None, False

    for col in sorted(numeric_cols, key=len, reverse=True):
        col_l = col.lower()
        for variant in {col_l, col_l.replace("_", " ")}:
            if len(variant) >= 3 and re.search(r"\b" + re.escape(variant) + r"\b", q_lower):
                return col, True
        # Loose containment for compound names ("unitprice" inside "unit price paid")
        col_norm = _norm(col)
        if len(col_norm) >= 5 and col_norm in _norm(q_lower):
            return col, True

    # Match on whole words from the question against every trigger word in the synonym
    # table - both its keys and the words inside each synonym list - tolerating a
    # trailing plural/singular "s" either side ("sale" <-> "sales", "unit" <-> "units")
    # so the dictionary does not need an entry for every grammatical variant.
    all_trigger_words = set(_METRIC_SYNONYMS.keys())
    for syns in _METRIC_SYNONYMS.values():
        all_trigger_words.update(syns)

    # A snake_case token like "shipping_cost" names one SPECIFIC field. If the exact-name
    # match above already failed to find a real column for it, that specific field simply
    # isn't in this dataset - falling back to a generic one-word synonym on "cost" alone
    # would silently substitute an unrelated column (e.g. UnitPrice) for a field that was
    # never there, exactly the kind of confident wrong answer this resolver exists to
    # avoid. So words that were part of such a compound are excluded from the loose
    # synonym fallback below; only bare, free-standing words reach it.
    locked_words = set()
    for compound in re.findall(r"\b[a-z]+_[a-z_]+\b", q_lower):
        locked_words.update(compound.split("_"))

    q_words = [w for w in re.findall(r"[a-z]+", q_lower) if w not in locked_words]
    for w in q_words:
        for form in (w, w.rstrip("s"), w + "s"):
            if form not in all_trigger_words:
                continue
            # This word matched some bucket - collect every synonym set it belongs to
            # (as a key or as a listed synonym) and check each real column against all
            # of them, so "unit"/"units" reaches the same "quantity" bucket either way.
            buckets = [key for key, syns in _METRIC_SYNONYMS.items()
                      if key in (form,) or form in syns]
            for key in buckets:
                for col in numeric_cols:
                    if any(syn in col.lower() for syn in _METRIC_SYNONYMS[key]):
                        return col, False

    # A non-technical user's typo ("revenu" for "Revenue") should still resolve - tried
    # last, and only against real column names (not the synonym table, to avoid a typo
    # accidentally drifting onto an unrelated bucket) with a conservative cutoff.
    col_names_lower = [c.lower() for c in numeric_cols]
    for w in q_words:
        if len(w) < 4:
            continue
        close = difflib.get_close_matches(w, col_names_lower, n=1, cutoff=0.8)
        if close:
            return numeric_cols[col_names_lower.index(close[0])], True

    return None, False


def resolve_groupby_column(q_lower: str, categorical_cols: list, exclude: str = None):
    """
    Which categorical column should a ranking/grouping/comparison question group by?

    Three patterns, tried in this order because natural language is ambiguous about
    which side of "by" is the group key:

      "top N <entity> BY <metric>"   - the entity (what's being ranked) is BEFORE "by";
                                        the word after "by" is the SORT metric, not the
                                        group column. ("top 3 sales reps by revenue"
                                        groups by SalesRep, not by Revenue.)
      "group by <X>" / "per <X>" /
      "for each <X>" / "<agg> by <X>" - X, right after "by"/"per", IS the group key.
                                        ("revenue by city", "count by product")
      bare mention of the column name  - "which city has the highest revenue"
    """
    candidates = [c for c in categorical_cols if c != exclude]
    if not candidates:
        return None

    def match_phrase(phrase):
        phrase_norm = _norm(phrase)
        if not phrase_norm:
            return None
        phrase_sing = _singularize(phrase_norm)
        for col in sorted(candidates, key=lambda c: -len(c)):
            col_norm = _norm(col)
            if col_norm and (col_norm == phrase_norm
                             or _singularize(col_norm) == phrase_sing):
                return col
        for col in sorted(candidates, key=lambda c: -len(c)):
            col_norm = _norm(col)
            if len(col_norm) >= 4 and (col_norm in phrase_norm or phrase_norm in col_norm):
                return col
        return None

    m = re.search(r"\b(?:top|highest|lowest|most|best|worst)\b\s*\d*\s*"
                  r"([a-z][a-z0-9_ ]{2,30}?)\s+by\b", q_lower)
    if m:
        hit = match_phrase(m.group(1))
        if hit:
            return hit

    m = re.search(r"\b(?:group(?:ed)?\s+by|per|for each|by)\s+([a-z][a-z0-9_ ]{1,30})", q_lower)
    if m:
        hit = match_phrase(m.group(1))
        if hit:
            return hit

    for col in sorted(candidates, key=lambda c: -len(c)):
        col_l = col.lower()
        for variant in {col_l, col_l.replace("_", " ")}:
            if len(variant) >= 3 and re.search(r"\b" + re.escape(variant) + r"\b", q_lower):
                return col
        col_norm = _norm(col)
        q_norm = _norm(q_lower)
        if len(col_norm) >= 5 and (col_norm in q_norm or _singularize(col_norm) in q_norm):
            return col

    return None


# (phrase, sql_operator). Order matters: longer/more specific phrases are listed first so
# "at least" is not pre-empted by a looser later pattern.
_COMPARISON_PHRASES = [
    (r"at least", ">="), (r"minimum of", ">="),
    (r"at most", "<="), (r"maximum of", "<="),
    (r"more than", ">"), (r"greater than", ">"), (r"over", ">"), (r"above", ">"), (r"exceeding", ">"),
    (r"less than", "<"), (r"fewer than", "<"), (r"under", "<"), (r"below", "<"),
    (r"exactly", "="), (r"equal to", "="),
]


def extract_numeric_comparisons(q_lower: str, numeric_cols: list) -> list:
    """
    Find "<column word(s)> <over/under/at least/...> <number>" style conditions and tie
    each one to a real numeric column, generically - not a hardcoded field name.

    Returns a list of (column, operator, value) tuples for every comparison recognised.
    A comparison whose column word doesn't resolve to any real numeric column is dropped
    silently by this function; the caller is responsible for treating an unresolved
    comparison as reportable rather than as "no constraint was intended".
    """
    results = []
    for phrase, op in _COMPARISON_PHRASES:
        pattern = r"\b([a-z][a-z_]*(?:\s+[a-z][a-z_]*)?)\s+(?:is\s+|was\s+)?" + phrase + r"\s+(\d+(?:\.\d+)?)"
        for m in re.finditer(pattern, q_lower):
            col_phrase, num = m.group(1), m.group(2)
            col, _ = resolve_metric_column(col_phrase, numeric_cols)
            if col:
                results.append((col, op, float(num)))
    return results
