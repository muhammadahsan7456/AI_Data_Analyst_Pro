"""
Structured Query Plan Engine
Converts natural language user questions into structured Query Plans before SQL generation:
{
    "intent": "retrieve_records" | "aggregate" | "count" | "rank",
    "table_name": str,
    "select": list,
    "computed_fields": list,
    "filters": list,
    "group_by": list,
    "order_by": list,
    "limit": int/None,
    "concept_validation": {"is_valid": bool, "missing_concepts": list}
}
"""

import os
import sys
import re
import pandas as pd
from ai.schema_intelligence import get_dataset_schema_intelligence
from ai.value_matcher import extract_multi_value_filters, find_unresolved_proper_nouns
from utils.cache import system_cache
from ai.metric_resolver import (
    get_real_numeric_columns,
    get_real_categorical_columns,
    resolve_metric_column,
    resolve_groupby_column,
    extract_numeric_comparisons,
    is_id_like_column,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12
}


def _extract_date_token(text_segment: str, end_of_month: bool = False):
    """
    Find a date-like substring in free text and normalize it to YYYY-MM-DD.
    Returns None if no parseable date is found, rather than guessing.

    A bare "Month YYYY" with no day number (e.g. "March 2025") is a common way to name
    one endpoint of a range - "between January 2025 and March 2025" - and used to match
    nothing at all here, since every pattern below required an explicit day. That silently
    dropped the whole range filter down to whatever the OTHER, unrelated bare-month-year
    pattern further down this function picked up instead (just "January", ignoring "and
    March 2025" completely). It resolves to the 1st of that month, unless `end_of_month`
    is set - the closing side of a range names a month meaning "through the end of it",
    not literally its first day, so a BETWEEN clause built from two 1st-of-month dates
    would silently exclude nearly the entire final month.
    """
    if not text_segment:
        return None
    patterns = [
        r"\d{4}-\d{1,2}-\d{1,2}",
        r"\d{1,2}/\d{1,2}/\d{4}",
        r"\d{1,2}-\d{1,2}-\d{4}",
        r"[A-Za-z]+\s+\d{1,2},?\s+\d{4}",
        r"\d{1,2}\s+[A-Za-z]+\s+\d{4}",
    ]
    for pat in patterns:
        m = re.search(pat, text_segment)
        if m:
            try:
                parsed = pd.to_datetime(m.group(0), errors="raise")
                return parsed.strftime("%Y-%m-%d")
            except Exception:
                continue

    bare_month = re.search(r"\b(" + "|".join(_MONTH_NAMES.keys()) + r")\s+(20\d{2})\b", text_segment)
    if bare_month:
        try:
            period = pd.Period(f"{bare_month.group(2)}-{_MONTH_NAMES[bare_month.group(1)]:02d}", freq="M")
            target = period.end_time if end_of_month else period.start_time
            return target.strftime("%Y-%m-%d")
        except Exception:
            pass

    return None


# Session memory for follow-up conversational context
_conversational_history = {}


def set_conversational_context(user_id: int, filters: list, select_cols: list):
    """Saves preceding query filters for conversational follow-ups."""
    if user_id:
        _conversational_history[user_id] = {
            "filters": filters,
            "select_cols": select_cols
        }


def get_conversational_context(user_id: int) -> dict:
    """Retrieves preceding query context for follow-up questions."""
    return _conversational_history.get(user_id, {})


def validate_concept_availability(question: str, schema_intel: dict) -> dict:
    """
    Validates whether the requested concepts exist in the dataset schema.
    If user asks about completely unrelated subjects (e.g., weather, capitals, personal advice),
    marks it as non-dataset query to return a polite dataset-specific boundary response.
    """
    q_lower = question.lower().strip()
    cols = [c.lower() for c in schema_intel.get("column_list", [])]

    # Unrelated general knowledge check
    general_knowledge_kws = ["capital of", "who is the president", "tell me a joke", "what is python", "write a poem", "weather in", "how to code", "my personal name", "personal name", "tell me my name", "who am i", "my age"]
    if any(kw in q_lower for kw in general_knowledge_kws):
        return {
            "is_valid": False,
            "is_unrelated": True,
            "missing_concepts": ["dataset context"]
        }

    missing = []
    if any(kw in q_lower for kw in ["sales", "revenue", "amount", "price", "spending"]) and not any(kw in c for c in cols for kw in ["sales", "revenue", "amount", "price", "cod", "value", "cost", "spend"]):
        missing.append("sales/revenue/amount")

    if any(kw in q_lower for kw in ["return", "returned", "delivery status", "order status"]) and not any(kw in c for c in cols for kw in ["status", "state", "return", "delivery"]):
        missing.append("order status / returned orders")

    if any(kw in q_lower for kw in ["product", "item", "sku"]) and not any(kw in c for c in cols for kw in ["product", "item", "sku", "category"]):
        missing.append("product/item")

    return {
        "is_valid": len(missing) == 0,
        "is_unrelated": False,
        "missing_concepts": missing
    }


def parse_question_to_query_plan(question: str, table_name: str) -> dict:
    """
    Constructs a structured T-SQL Query Plan for Microsoft SQL Server.
    """
    if not question:
        return {}

    cache_key = f"qplan_{table_name}_{hash(question.strip().lower())}"
    if system_cache:
        cached_plan = system_cache.get(cache_key)
        if cached_plan is not None:
            return cached_plan

    schema_intel = get_dataset_schema_intelligence(table_name)
    column_list = schema_intel.get("column_list", [])
    cols_meta = schema_intel.get("columns", {})
    q_lower = question.lower().strip()

    # The dataset's REAL numeric/categorical columns (from actual pandas dtype and value
    # cardinality - ai/schema_intelligence.py), used everywhere below instead of matching
    # column names against a fixed English keyword list. See ai/metric_resolver.py for why:
    # a keyword list only ever covers the columns whose name happens to contain one of a
    # handful of hardcoded words, so "Revenue", "SalesRep" or any other real column name
    # outside that list used to be invisible to aggregation/grouping detection entirely.
    numeric_cols_real = get_real_numeric_columns(schema_intel)
    categorical_cols_real = get_real_categorical_columns(schema_intel)

    # Collected here and returned in the plan so the caller (gemini.py) can tell the user
    # plainly when something they asked about isn't in this dataset, instead of silently
    # substituting a different column/dropping the constraint and returning a confident
    # but wrong answer.
    unresolved_notices = []

    concept_check = validate_concept_availability(question, schema_intel)

    filters = []
    select_cols = []
    computed_fields = []
    order_by = []
    group_by = []
    limit = None

    # 1. LIMIT, TOP & OFFSET RANGE CLAUSE DETECTION
    range_match = re.search(r"\b(?:from|records|rows)?\s*(\d{1,6})\s*(?:to|till|until|-)\s*(\d{1,6})\b", q_lower)
    if range_match:
        start_idx = int(range_match.group(1))
        end_idx = int(range_match.group(2))
        if end_idx >= start_idx:
            limit = end_idx - start_idx + 1

    if not range_match:
        top_match = re.search(r"\b(?:top|first|last|aakhri|pehle|recent|newest)\s*(\d+)\b", q_lower)
        if top_match:
            limit = int(top_match.group(1))
        elif any(kw in q_lower for kw in ["first 10", "pehle 10"]):
            limit = 10
        elif any(kw in q_lower for kw in ["last 10", "aakhri 10"]):
            limit = 10

    # 2. COMPUTED FIELD & NAME RESOLUTION (Full Name -> First Name + Last Name)
    fname_col = next((c for c in column_list if c.lower() in ["first name", "firstname", "first_name"]), None)
    lname_col = next((c for c in column_list if c.lower() in ["last name", "lastname", "last_name"]), None)

    if any(kw in q_lower for kw in ["full name", "fullname", "name", "names"]) and fname_col and lname_col:
        computed_fields.append({
            "name": "Full Name",
            "expression": f"CONCAT([{fname_col}], ' ', [{lname_col}])"
        })
        select_cols.append("Full Name")

    # Select requested explicit columns
    for col in column_list:
        c_lower = col.lower()
        c_clean = c_lower.replace("_", " ")
        if c_lower in q_lower or c_clean in q_lower or (c_lower == "company" and "company" in q_lower) or (c_lower == "city" and "city" in q_lower) or (c_lower == "email" and "email" in q_lower) or (c_lower == "country" and "country" in q_lower) or ("subscription" in q_lower and "date" in c_lower):
            if col not in select_cols and col != fname_col and col != lname_col:
                select_cols.append(col)

    if not select_cols and not computed_fields:
        select_cols = column_list[:8]

    # 3. VALUE FILTERS - field-agnostic, learned from the dataset itself.
    #
    # This replaces three hardcoded vocabularies that used to live here: a list of 20
    # country names (which `break`s after the first hit), a dict of 8 Pakistani cities,
    # and an if/elif chain of status keywords. Each of them could only ever contribute
    # ONE value, and none of them knew about any other column - products, categories,
    # channels, sales reps and every other field were simply invisible to the planner.
    #
    # ai/value_matcher.py reads each low-cardinality column's real distinct values out of
    # the dataset and matches every one the question mentions, emitting `[col] IN (...)`.
    # It is the same code path for every column, so a question naming two products, three
    # statuses or four cities all behave identically without anyone maintaining a list.
    value_filters = extract_multi_value_filters(question, table_name, schema_intel)
    filters.extend(value_filters)

    # Proper-noun-like words that don't match anything in this dataset ("Sialkot" when
    # the City column only has 6 other cities) used to be silently ignored - the filter
    # was simply never added, so the query ran unfiltered and returned a confident count
    # of the WHOLE table as if the word had never been typed. Recording them here lets the
    # caller say plainly that the value wasn't found instead of guessing.
    unresolved_notices.extend(
        f"'{term}' was not found in this dataset." for term in
        find_unresolved_proper_nouns(question, table_name, schema_intel)
    )

    # A comparison across several values of one field ("A vs B", "compare X and Y") should
    # come back grouped by that field so each value's numbers are visible separately
    # instead of merged into one total. Deliberately generic: it keys off whichever column
    # the multiple values belong to, not off any particular field name.
    is_comparison_question = any(kw in q_lower for kw in
                                 ["compare", "comparison", "vs", "versus", "muqabla", "difference"])
    if is_comparison_question:
        multi = next((f for f in value_filters if len(f.get("values", [])) >= 2), None)
        if multi:
            # Comparing several values of one field ("compare revenue between Karachi and
            # Lahore") needs the ACTUAL metric named in the question, not just a row count -
            # resolve it the same schema-driven way every other aggregation does, so this
            # works for whichever numeric column the question names.
            # The filter stays in place (WHERE City IN ('Karachi','Lahore')) - it restricts
            # the comparison to just the named values; GROUP BY then breaks THOSE rows down
            # per value instead of also including every other city in the dataset.
            cmp_metric_col, _ = resolve_metric_column(q_lower, numeric_cols_real)
            group_by = [multi["column"]]
            computed_fields = [{"name": "Record_Count", "expression": "COUNT(*)"}]
            if cmp_metric_col:
                safe_expr = f"TRY_CAST(REPLACE(CAST([{cmp_metric_col}] AS NVARCHAR(MAX)), ',', '') AS FLOAT)"
                sum_name = f"Total_{cmp_metric_col}"
                computed_fields.append({"name": sum_name, "expression": f"SUM({safe_expr})"})
                order_by = [{"column": sum_name, "sql_safe_column": f"[{sum_name}]", "direction": "DESC"}]
            else:
                order_by = [{"column": "Record_Count", "sql_safe_column": "[Record_Count]", "direction": "DESC"}]
            select_cols = [multi["column"]]


    # 4. DATE RANGE / COMPARISON / MONTH+YEAR / BARE YEAR FILTERS
    # Previously this only ever detected a bare 4-digit year and did YEAR(col) = year,
    # so "between X and Y", "after X", "before X" and "in <month> <year>" all silently
    # fell through to the year-only branch (or matched nothing) and returned the WHOLE
    # table unfiltered - the actual date constraint the user asked for was ignored.
    date_col = next((c for c in column_list if any(kw in c.lower() for kw in ["date", "subscribed", "year", "created"])), None)
    if date_col:
        date_filter_applied = False

        range_match = re.search(
            r"(?:between|from)\s+(.+?)\s+(?:and|to)\s+([\w,\s/-]+?)(?=$|[.?]|,|\s+(?:for|in|where)\b)",
            q_lower
        )
        if range_match:
            d1 = _extract_date_token(range_match.group(1))
            d2 = _extract_date_token(range_match.group(2), end_of_month=True)
            if d1 and d2:
                filters.append({
                    "column": date_col,
                    "sql_safe_column": f"[{date_col}]",
                    "operator": "DATE_BETWEEN",
                    "value": f"{d1} to {d2}",
                    "sql_clause": f"TRY_CAST([{date_col}] AS DATE) BETWEEN ? AND ?",
                    "params": [d1, d2]
                })
                date_filter_applied = True

        if not date_filter_applied:
            # "since"/"from" read as inclusive of the given date (>=); "after" reads as
            # strictly exclusive (>) - matches how these words are normally used.
            since_match = re.search(r"\b(?:since|from)\s+([\w,\s/-]+?)(?=$|[.?]|,)", q_lower)
            after_match = re.search(r"\bafter\s+([\w,\s/-]+?)(?=$|[.?]|,)", q_lower)
            before_match = re.search(r"\b(?:before|until|till)\s+([\w,\s/-]+?)(?=$|[.?]|,)", q_lower)
            if since_match:
                d = _extract_date_token(since_match.group(1))
                if d:
                    filters.append({
                        "column": date_col,
                        "sql_safe_column": f"[{date_col}]",
                        "operator": "DATE_SINCE",
                        "value": d,
                        "sql_clause": f"TRY_CAST([{date_col}] AS DATE) >= ?",
                        "params": [d]
                    })
                    date_filter_applied = True
            elif after_match:
                d = _extract_date_token(after_match.group(1))
                if d:
                    filters.append({
                        "column": date_col,
                        "sql_safe_column": f"[{date_col}]",
                        "operator": "DATE_AFTER",
                        "value": d,
                        "sql_clause": f"TRY_CAST([{date_col}] AS DATE) > ?",
                        "params": [d]
                    })
                    date_filter_applied = True
            elif before_match:
                d = _extract_date_token(before_match.group(1))
                if d:
                    filters.append({
                        "column": date_col,
                        "sql_safe_column": f"[{date_col}]",
                        "operator": "DATE_BEFORE",
                        "value": d,
                        "sql_clause": f"TRY_CAST([{date_col}] AS DATE) < ?",
                        "params": [d]
                    })
                    date_filter_applied = True

        if not date_filter_applied:
            month_year_match = re.search(r"\b(" + "|".join(_MONTH_NAMES.keys()) + r")\s+(20\d{2})\b", q_lower)
            if month_year_match:
                m_num = _MONTH_NAMES[month_year_match.group(1)]
                y_num = int(month_year_match.group(2))
                filters.append({
                    "column": date_col,
                    "sql_safe_column": f"[{date_col}]",
                    "operator": "MONTH_YEAR_EQUALS",
                    "value": f"{y_num}-{m_num:02d}",
                    "sql_clause": f"YEAR(TRY_CAST([{date_col}] AS DATE)) = ? AND MONTH(TRY_CAST([{date_col}] AS DATE)) = ?",
                    "params": [y_num, m_num]
                })
                date_filter_applied = True

        if not date_filter_applied:
            year_match = re.search(r"\b(20[1-9]\d)\b", q_lower)
            if year_match:
                target_year = int(year_match.group(1))
                filters.append({
                    "column": date_col,
                    "sql_safe_column": f"[{date_col}]",
                    "operator": "YEAR_EQUALS",
                    "value": target_year,
                    "sql_clause": f"YEAR([{date_col}]) = ?",
                    "params": [target_year]
                })

    # 5. DETERMINISTIC SORTING RESOLUTION (Newest/Latest/Oldest/First/Last)
    id_or_index_col = next((c for c in column_list if c.lower() in ["recordid", "index", "id"]), column_list[0] if column_list else "RecordID")

    if date_col and any(kw in q_lower for kw in ["newest", "latest", "recent", "new"]):
        order_by.append({"column": date_col, "sql_safe_column": f"[{date_col}]", "direction": "DESC"})
    elif date_col and any(kw in q_lower for kw in ["oldest", "earliest"]):
        order_by.append({"column": date_col, "sql_safe_column": f"[{date_col}]", "direction": "ASC"})
    elif any(kw in q_lower for kw in ["last", "aakhri"]):
        order_by.append({"column": id_or_index_col, "sql_safe_column": f"[{id_or_index_col}]", "direction": "DESC"})
    elif any(kw in q_lower for kw in ["first", "pehle", "initial"]):
        order_by.append({"column": id_or_index_col, "sql_safe_column": f"[{id_or_index_col}]", "direction": "ASC"})

    intent = "retrieve_records"

    # 6. SCHEMA-DRIVEN AGGREGATION INTENT RESOLUTION
    #
    # This whole section used to decide "which numeric column does 'total X' mean" and
    # "which categorical column does 'top N Y' mean" using two fixed English keyword
    # lists - numeric candidates only if the COLUMN NAME contained one of a handful of
    # words like "cod"/"amount"/"price", categorical candidates only if it contained one
    # of "destination"/"status"/"city"/"country"/"company"/"category"/"type"/"state". A
    # column named "Revenue" or "SalesRep" matches neither list, so "total revenue"
    # silently summed whatever OTHER numeric column happened to be first in the table,
    # and "top 3 sales reps by revenue" silently grouped by the "Revenue" column itself
    # (the only column name literally present in the question) instead of by SalesRep.
    # Both are confident, plausible-looking WRONG answers - worse than an error.
    #
    # ai/metric_resolver.py resolves both kinds of column against the dataset's REAL
    # schema (numeric columns from actual pandas dtype, categorical columns from actual
    # value cardinality), so this works for whichever columns a user's dataset has, not
    # just the ones on a hardcoded list. The bare "count"/"total" catch-all that used to
    # run FIRST and pre-empt every other check now runs LAST, only once every more
    # specific aggregation/ranking pattern has had a chance to match.
    metric_col, metric_exact = resolve_metric_column(q_lower, numeric_cols_real)

    # Sub-city area breakdown ("Gulshan"/"Saddar"/"DHA" within Karachi) parses free-text
    # address values rather than grouping by a column's existing distinct values, so it
    # cannot go through the normal categorical-column resolver above - kept as its own
    # narrow check, same as before.
    group_col = None

    # Step A: Check for explicit pattern like "... wise", "by ...", "... ke hisab se"
    wise_target = None
    wise_m = re.search(r"\b([a-z_]{3,30})[\s-]wise\b", q_lower)
    by_m = re.search(r"\b(?:by|per|for\s+each)\s+([a-z_]{3,30})\b", q_lower)
    hisab_m = re.search(r"\b([a-z_]{3,30})\s+(?:ke\s+)?(?:hisab|hissab)\b", q_lower)

    if wise_m:
        wise_target = wise_m.group(1).lower()
    elif by_m:
        wise_target = by_m.group(1).lower()
    elif hisab_m:
        wise_target = hisab_m.group(1).lower()

    # Step B: If the target is area/locality or query mentions sub-areas and address column exists
    address_col = next((c for c in column_list if any(kw in c.lower() for kw in
                        ["address", "street", "consignee_address", "location"])), None)
    is_area_query = (wise_target and any(kw in wise_target for kw in ["area", "locality", "neighborhood", "subcity", "place"])) or \
                    any(kw in q_lower for kw in ["area", "areas", "locality", "localities", "neighborhood", "neighborhoods",
                         "sub-city", "gulshan", "saddar", "defence", "dha", "clifton", "johar", "korangi", "jahan jahan"])

    if is_area_query:
        if address_col:
            from ai.area_intelligence import build_sql_sub_area_case_statement
            known_pk_cities = ["karachi", "lahore", "islamabad", "rawalpindi", "multan", "peshawar",
                               "faisalabad", "quetta", "hyderabad", "sukkur", "sialkot", "gujranwala"]
            target_city_name = next((c.title() for c in known_pk_cities if c in q_lower), None)
            group_col = build_sql_sub_area_case_statement(address_col, target_city_name)
        else:
            # Table doesn't have an address column, check for City/Destination/Area column
            area_direct_col = next((c for c in column_list if any(kw in c.lower() for kw in ["area", "city", "destination", "location", "region", "country"])), None)
            if area_direct_col:
                group_col = area_direct_col

    # Step C: If wise_target was another column concept (e.g. "country", "department", "company", "status", "category", "product", "role")
    if not group_col and wise_target:
        for c in column_list:
            c_low = c.lower()
            if wise_target == c_low or wise_target in c_low or c_low in wise_target:
                if c != metric_col and not is_id_like_column(c):
                    group_col = c
                    break

    # Step D: Schema-driven categorical resolver
    if not group_col:
        group_col = resolve_groupby_column(q_lower, categorical_cols_real, exclude=metric_col)

    # Step E: Direct column name mention fallback
    if not group_col:
        for c in column_list:
            if c != metric_col and not is_id_like_column(c) and len(c) >= 3:
                c_clean = c.lower().replace("_", " ")
                if re.search(r"\b" + re.escape(c.lower()) + r"\b", q_lower) or re.search(r"\b" + re.escape(c_clean) + r"\b", q_lower):
                    group_col = c
                    break

    # Numeric threshold filters ("quantity over 1000", "revenue at least 500") - generic,
    # tied to whichever real numeric column the comparison phrase names. Previously there
    # was no support for this at all: a clause like "with quantity over 1000" was simply
    # dropped, silently widening the result set to everything that matched the OTHER
    # filters instead of either applying the constraint or saying it couldn't be applied.
    for cmp_col, cmp_op, cmp_val in extract_numeric_comparisons(q_lower, numeric_cols_real):
        safe_cmp_expr = f"TRY_CAST(REPLACE(CAST([{cmp_col}] AS NVARCHAR(MAX)), ',', '') AS FLOAT)"
        filters.append({
            "column": cmp_col,
            "sql_safe_column": f"[{cmp_col}]",
            "operator": cmp_op,
            "value": cmp_val,
            "sql_clause": f"{safe_cmp_expr} {cmp_op} ?",
            "params": [cmp_val],
        })

    wants_sum = bool(re.search(r"\b(?:total|sum(?:\s+of)?)\b", q_lower))
    wants_avg = bool(re.search(r"\b(?:average|avg|mean)\b", q_lower))
    wants_min = bool(re.search(r"\bminimum\b|\bmin\b", q_lower))
    wants_max = bool(re.search(r"\bmaximum\b|\bmax\b", q_lower))
    wants_distinct = "distinct" in q_lower
    wants_count = bool(re.search(r"\b(?:count|how many|kitne|kitni|number of)\b", q_lower))
    wants_percentage = any(kw in q_lower for kw in ["percentage", "percent", "%", "ratio", "fraction"])
    wants_ranking = (
        bool(re.search(r"\b(?:top|highest|lowest|most|best|worst|least|rank(?:ing)?)\b", q_lower))
        or bool(re.search(r"\b(?:group(?:ed)?\s+by|per|for\s+each|breakdown|distribution|split|share|ratio)\b", q_lower))
        or bool(re.search(r"\b\w+[\s-]wise\b|\bwise\b", q_lower))
        or bool(re.search(r"\b(?:by)\s+[a-z_]+", q_lower))
        or bool(re.search(r"\b(?:hisab\s+se|hissab\s+se|ke\s+mutabiq|tafseel)\b", q_lower))
        or bool(re.search(r"\b(?:jahan\s+jahan\s+se|kidhar\s+kidhar\s+se|kahan\s+kahan\s+se|kon\s+kon\s+se|kaun\s+kaun\s+se|kis\s+kis\b)", q_lower))
        or (group_col is not None and not any(kw in q_lower for kw in ["first", "pehle", "last", "aakhri"]))
    )

    # A clear aggregation-function word (total/sum/average/min/max) but no real column to
    # apply it to is exactly the "what is the total shipping_cost" case: say so plainly
    # instead of silently substituting a different numeric column and reporting a
    # confident, unrelated number.
    if (wants_sum or wants_avg or wants_min or wants_max) and not metric_col and numeric_cols_real and not wants_count:
        m = re.search(r"\b(?:total|sum|average|avg|mean|minimum|min|maximum|max)\s+(?:of\s+)?([a-z][a-z0-9_]{2,30})", q_lower)
        term = m.group(1) if m else question.strip()
        unresolved_notices.append(f"No column matching '{term}' was found in this dataset.")

    if any(kw in q_lower for kw in ["missing value", "missing values", "null value", "null values", "empty cells", "missing data"]):
        intent = "missing_values"
        select_cols, computed_fields, order_by, limit = [], [], [], None
        for col in column_list[:12]:
            if col.lower() not in ["recordid"]:
                computed_fields.append({
                    "name": f"{col}_Nulls",
                    "expression": f"SUM(CASE WHEN [{col}] IS NULL THEN 1 ELSE 0 END)"
                })
    elif any(kw in q_lower for kw in ["duplicate customer id", "duplicate customer ids", "duplicate ids", "duplicate records", "duplicates", "duplicate entries", "duplicate values", "which columns contain duplicate"]):
        intent = "duplicate_records"
        business_cols = [c for c in column_list if c.lower() not in ["recordid", "index", "sr"]]
        # Prioritize business fields that frequently have duplicates like contact, phone, product, name, cnic, email
        target_id_col = next((c for c in business_cols if any(kw in c.lower() for kw in ["contact", "phone", "product", "cnic", "name", "email", "address"])), business_cols[0] if business_cols else "Consignee_Contact")
        select_cols = [target_id_col]
        group_by = [target_id_col]
        computed_fields = [{"name": "Duplicate_Count", "expression": "COUNT(*)"}]
        order_by = [{"column": "Duplicate_Count", "sql_safe_column": "[Duplicate_Count]", "direction": "DESC"}]
        limit = 100
    elif "earliest" in q_lower and "latest" in q_lower and date_col:
        intent = "min_max_date"
        select_cols, computed_fields, order_by = [], [], []
        computed_fields.append({"name": "Earliest Subscription Date", "expression": f"MIN([{date_col}])"})
        computed_fields.append({"name": "Latest Subscription Date", "expression": f"MAX([{date_col}])"})
    elif wants_percentage and any(kw in q_lower for kw in ["return", "deliver"]) and not group_col:
        # This narrower status-specific percentage path is left as it was; it is not one
        # of the aggregation-column bugs fixed this pass (it doesn't pick a numeric
        # column at all, only a status literal), and rewriting it is out of scope here.
        intent = "percentage_calc"
        select_cols, computed_fields, order_by = [], [], []
        filters = []  # Clear global filters so denominator COUNT(*) spans total dataset
        status_col = next((c for c in column_list if any(kw in c.lower() for kw in ["status", "delivery", "state"])), "Delivery_Status")
        if "return" in q_lower:
            computed_fields.append({"name": "Matching_Orders", "expression": f"COUNT(CASE WHEN [{status_col}] = 'Returned' OR [{status_col}] LIKE '%RETURN%' THEN 1 END)"})
            computed_fields.append({"name": "Total_Orders", "expression": "COUNT(*)"})
            computed_fields.append({"name": "Percentage", "expression": f"ROUND(CAST(COUNT(CASE WHEN [{status_col}] = 'Returned' OR [{status_col}] LIKE '%RETURN%' THEN 1 END) AS FLOAT) * 100.0 / NULLIF(COUNT(*), 0), 2)"})
        else:
            computed_fields.append({"name": "Matching_Orders", "expression": f"COUNT(CASE WHEN [{status_col}] = 'Delivered' OR [{status_col}] LIKE '%DELIVER%' THEN 1 END)"})
            computed_fields.append({"name": "Total_Orders", "expression": "COUNT(*)"})
            computed_fields.append({"name": "Percentage", "expression": f"ROUND(CAST(COUNT(CASE WHEN [{status_col}] = 'Delivered' OR [{status_col}] LIKE '%DELIVER%' THEN 1 END) AS FLOAT) * 100.0 / NULLIF(COUNT(*), 0), 2)"})
    elif wants_ranking and group_col:
        # "top N <entity> by <metric>", "which <entity> has the highest <metric>",
        # "<metric> by <entity>", "breakdown by <entity>" - group_col is what's being
        # ranked/broken down, resolved above independently of which side of "by" it's on.
        intent = "aggregate_rank"
        select_cols = [group_col]
        group_by = [group_col]
        computed_fields = []
        safe_metric_expr = (f"TRY_CAST(REPLACE(CAST([{metric_col}] AS NVARCHAR(MAX)), ',', '') AS FLOAT)"
                            if metric_col else None)

        if wants_avg and safe_metric_expr:
            sort_field = f"Average_{metric_col}"
            computed_fields.append({"name": sort_field, "expression": f"ROUND(AVG({safe_metric_expr}), 2)"})
        elif safe_metric_expr and not wants_count:
            # A real metric column was actually named ("... by revenue") - rank by its
            # SUM by default. This is what "top N X by Y" means even when the question
            # never spells out the word "total"/"sum".
            sort_field = f"Total_{metric_col}"
            computed_fields.append({"name": sort_field, "expression": f"SUM({safe_metric_expr})"})
        else:
            sort_field = "Record_Count"
            computed_fields.append({"name": "Record_Count", "expression": "COUNT(*)"})

        if wants_percentage:
            computed_fields.append({"name": "Percentage_Of_Total", "expression": f"ROUND(CAST(COUNT(*) AS FLOAT) * 100.0 / NULLIF((SELECT COUNT(*) FROM [{table_name}]), 0), 2)"})

        direction = "ASC" if any(kw in q_lower for kw in ["lowest", "bottom", "least", "worst", "smallest"]) else "DESC"
        order_by = [{"column": sort_field, "sql_safe_column": f"[{sort_field}]", "direction": direction}]

        if limit is None:
            limit = 10 if any(kw in q_lower for kw in ["top", "best", "worst"]) else None
    elif metric_col and (wants_sum or wants_avg or wants_min or wants_max or wants_distinct):
        # A real numeric column was named/implied and an aggregation function was asked
        # for, with no grouping - a single-number answer ("total revenue for Laptop",
        # "average quantity for Online channel").
        intent = "aggregate_stat"
        select_cols, computed_fields, order_by = [], [], []
        safe_metric_expr = f"TRY_CAST(REPLACE(CAST([{metric_col}] AS NVARCHAR(MAX)), ',', '') AS FLOAT)"
        if wants_distinct:
            computed_fields.append({"name": f"Distinct_{metric_col}_Count", "expression": f"COUNT(DISTINCT [{metric_col}])"})
        elif wants_avg:
            computed_fields.append({"name": f"Average_{metric_col}", "expression": f"ROUND(AVG({safe_metric_expr}), 2)"})
        elif wants_min:
            computed_fields.append({"name": f"Minimum_{metric_col}", "expression": f"MIN({safe_metric_expr})"})
        elif wants_max:
            computed_fields.append({"name": f"Maximum_{metric_col}", "expression": f"MAX({safe_metric_expr})"})
        else:
            computed_fields.append({"name": f"Total_{metric_col}", "expression": f"SUM({safe_metric_expr})"})
    elif wants_distinct:
        # "how many distinct products" - X is a category, not a number.
        dist_col = next((c for c in column_list if c.lower() in q_lower and not is_id_like_column(c)), None)
        if dist_col:
            intent = "aggregate_stat"
            select_cols, order_by = [], []
            computed_fields = [{"name": f"Distinct_{dist_col}_Count", "expression": f"COUNT(DISTINCT [{dist_col}])"}]
    elif any(kw in q_lower for kw in ["records", "rows", "entries", "data", "ross", "raws", "rose"]) and not any(kw in q_lower for kw in ["by", "per", "group", "breakdown"]):
        intent = "retrieve_records"
        select_cols = [c for c in column_list[:10] if c.lower() != "recordid"]
        primary_pk = column_list[0] if column_list else "RecordID"
        direction = "DESC" if any(kw in q_lower for kw in ["last", "bottom", "aakhri", "latest", "recent"]) else "ASC"
        order_by = [{"column": primary_pk, "sql_safe_column": f"[{primary_pk}]", "direction": direction}]
        if limit is None:
            limit = 10
    elif wants_ranking and not group_col:
        # User wants top/highest/lowest individual records sorted by the metric or primary key
        intent = "retrieve_records"
        direction = "ASC" if any(kw in q_lower for kw in ["lowest", "bottom", "least", "worst", "smallest", "oldest"]) else "DESC"
        if metric_col:
            safe_metric_expr = f"TRY_CAST(REPLACE(CAST([{metric_col}] AS NVARCHAR(MAX)), ',', '') AS FLOAT)"
            order_by = [{"column": metric_col, "sql_safe_column": safe_metric_expr, "direction": direction}]
            if metric_col not in select_cols:
                select_cols.append(metric_col)
        elif date_col and any(kw in q_lower for kw in ["recent", "latest", "newest", "oldest"]):
            order_by = [{"column": date_col, "sql_safe_column": f"[{date_col}]", "direction": direction}]
        elif id_or_index_col:
            order_by = [{"column": id_or_index_col, "sql_safe_column": f"[{id_or_index_col}]", "direction": direction}]

        if limit is None:
            limit = 10
        if wants_count:
            intent = "count"
            select_cols, order_by = [], []
            computed_fields = [{"name": "Total_Orders", "expression": "COUNT(*)"}]
    elif wants_count:
        intent = "count"
        select_cols, order_by = [], []
        computed_fields = [{"name": "Total_Orders", "expression": "COUNT(*)"}]
    elif (intent == "retrieve_records" and metric_col and filters and not computed_fields
          and not any(kw in q_lower for kw in ["show", "list", "display", "dikhao", "records", "rows"])):
        # A bare "<filter values> <metric name>" phrasing with no explicit aggregation
        # word - e.g. "Karachi and Lahore revenue", or "revenu for laptp" (typo, caught
        # by metric_resolver's fuzzy match) - would otherwise dump every matching row's
        # raw value one at a time (thousands of rows for a broad filter). Idiomatically
        # this almost always means "how much <metric> did <filter> add up to", so it
        # defaults to a sum, the same as "total <metric> for <filter>" already does. The
        # "show/list/display/records" guard lets an explicit request for raw rows still
        # get raw rows instead of being coerced into a total.
        intent = "aggregate_stat"
        safe_metric_expr = f"TRY_CAST(REPLACE(CAST([{metric_col}] AS NVARCHAR(MAX)), ',', '') AS FLOAT)"
        computed_fields = [{"name": f"Total_{metric_col}", "expression": f"SUM({safe_metric_expr})"}]
        select_cols, order_by = [], []

    plan_res = {
        "intent": intent,
        "question": question,
        "table_name": table_name,
        "select": select_cols,
        "computed_fields": computed_fields,
        "filters": filters,
        "group_by": group_by,
        "order_by": order_by,
        "limit": limit,
        "concept_validation": concept_check,
        "unresolved": unresolved_notices
    }

    if system_cache:
        system_cache.set(cache_key, plan_res, ttl=300)

    return plan_res
