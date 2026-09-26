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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12
}


def _extract_date_token(text_segment: str):
    """
    Find a date-like substring in free text and normalize it to YYYY-MM-DD.
    Returns None if no parseable date is found, rather than guessing.
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
    schema_intel = get_dataset_schema_intelligence(table_name)
    column_list = schema_intel.get("column_list", [])
    cols_meta = schema_intel.get("columns", {})
    q_lower = question.lower().strip()

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

    # 3. COUNTRY, CITY & VALUE FILTERS (e.g. Korea, India, Karachi, Lahore, London, gmail, yahoo)
    country_col = next((c for c in column_list if "country" in c.lower()), None)
    matched_country = False
    if country_col:
        known_countries = ["korea", "india", "congo", "pakistan", "china", "usa", "united states", "japan", "germany", "brazil", "eritrea", "chile", "france", "uk", "united kingdom", "canada", "australia", "mexico", "spain", "italy"]
        for c_val in known_countries:
            if re.search(r"\b" + re.escape(c_val) + r"\b", q_lower):
                val_formatted = "Korea" if c_val == "korea" else c_val.title()
                filters.append({
                    "column": country_col,
                    "sql_safe_column": f"[{country_col}]",
                    "operator": "=",
                    "value": val_formatted,
                    "sql_clause": f"[{country_col}] = ?",
                    "params": [val_formatted]
                })
                matched_country = True
                break

    city_cols = [c for c in column_list if any(kw in c.lower() for kw in ["city", "origin", "destination", "address", "location"])]
    known_cities = {
        "karachi": "KHI",
        "lahore": "LHE",
        "islamabad": "ISB",
        "multan": "MUX",
        "peshawar": "PEW",
        "rawalpindi": "RWP",
        "quetta": "UET",
        "faisalabad": "FSD"
    }

    matched_cities = []
    for c_val, code in known_cities.items():
        if re.search(r"\b" + re.escape(c_val) + r"\b", q_lower) or re.search(r"\b" + re.escape(code.lower()) + r"\b", q_lower):
            matched_cities.append((c_val, code))

    if matched_cities and city_cols:
        # Pick the SINGLE BEST primary city column to avoid ORing origin and destination
        primary_city_col = None
        if "origin" in q_lower:
            primary_city_col = next((c for c in city_cols if "origin" in c.lower()), None)
        if not primary_city_col:
            primary_city_col = next((c for c in city_cols if any(k in c.lower() for k in ["destination_city", "dest_city", "destination", "customer_city", "shipping_city", "consignee_city", "city"])), city_cols[0])

        clauses = []
        clause_params = []
        for c_val, code in matched_cities:
            clauses.append(f"[{primary_city_col}] = ?")
            clause_params.append(c_val.title())
            clauses.append(f"[{primary_city_col}] = ?")
            clause_params.append(code)
            clauses.append(f"[{primary_city_col}] LIKE ?")
            clause_params.append(f"%{c_val.title()}%")
            clauses.append(f"[{primary_city_col}] LIKE ?")
            clause_params.append(f"%({code})%")

        filters.append({
            "column": primary_city_col,
            "sql_safe_column": f"[{primary_city_col}]",
            "operator": "LOCATION_MATCH",
            "value": matched_cities[0][0].title(),
            "sql_clause": f"({' OR '.join(clauses)})",
            "params": clause_params
        })
        if len(matched_cities) >= 2 and any(kw in q_lower for kw in ["compare", "vs", "versus"]):
            dest_col = next((c for c in city_cols if "destination" in c.lower()), city_cols[0])
            group_by = [dest_col]
            computed_fields = [{"name": "Order_Count", "expression": "COUNT(*)"}]
            order_by = [{"column": "Order_Count", "sql_safe_column": "[Order_Count]", "direction": "DESC"}]

    # 3.5 STATUS FILTERS (e.g. delivered, return, returned, pending, cancelled)
    status_cols = [c for c in column_list if any(kw in c.lower() for kw in ["status", "delivery", "state"])]
    if status_cols:
        status_clauses = []
        if any(kw in q_lower for kw in ["delivered", "deliver"]):
            for s_col in status_cols:
                status_clauses.append(f"[{s_col}] LIKE '%Delivered%'")
                status_clauses.append(f"[{s_col}] = 'DELIVERED'")
        elif any(kw in q_lower for kw in ["return", "returned", "returns", "rto"]):
            for s_col in status_cols:
                status_clauses.append(f"[{s_col}] LIKE '%Return%'")
                status_clauses.append(f"[{s_col}] LIKE '%RTO%'")
        elif any(kw in q_lower for kw in ["pending"]):
            for s_col in status_cols:
                status_clauses.append(f"[{s_col}] LIKE '%Pending%'")
        elif any(kw in q_lower for kw in ["cancel", "cancelled", "canceled"]):
            for s_col in status_cols:
                status_clauses.append(f"[{s_col}] LIKE '%Cancel%'")

        if status_clauses:
            filters.append({
                "column": status_cols[0],
                "sql_safe_column": f"[{status_cols[0]}]",
                "operator": "STATUS_MATCH",
                "value": "Status Filter",
                "sql_clause": f"({' OR '.join(status_clauses)})"
            })

    email_col = next((c for c in column_list if "email" in c.lower()), None)
    if email_col:
        domains = ["gmail", "yahoo", "hotmail", "outlook", "icloud", "protonmail"]
        for dom in domains:
            if dom in q_lower:
                filters.append({
                    "column": email_col,
                    "sql_safe_column": f"[{email_col}]",
                    "operator": "LIKE",
                    "value": dom,
                    "sql_clause": f"[{email_col}] LIKE ?",
                    "params": [f"%{dom}%"]
                })
                break

    if not matched_country and not filters:
        for col, meta in cols_meta.items():
            sample_vals = meta.get("sample_values", [])
            for val in sample_vals:
                val_lower = str(val).lower().strip()
                if len(val_lower) >= 3 and val_lower in q_lower and val_lower not in ["min", "max", "avg", "sum", "top", "this", "that"]:
                    filters.append({
                        "column": col,
                        "sql_safe_column": f"[{col}]",
                        "operator": "=",
                        "value": val,
                        "sql_clause": f"[{col}] = ?",
                        "params": [val]
                    })
                    break

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
            d2 = _extract_date_token(range_match.group(2))
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
    
    # 6. COUNT / SUMMARY AGGREGATE INTENT RESOLUTION
    if any(kw in q_lower for kw in ["count", "total", "how many", "kitne", "kitni", "number of"]) and not any(kw in q_lower for kw in ["by", "group", "compare", "breakdown", "per"]):
        intent = "count"
        computed_fields = [{"name": "Total_Orders", "expression": "COUNT(*)"}]
        select_cols = []
        order_by = []
    elif any(kw in q_lower for kw in ["missing value", "missing values", "null value", "null values", "empty cells", "missing data"]):
        intent = "missing_values"
        select_cols = []
        for col in column_list[:12]:
            if col.lower() not in ["recordid"]:
                computed_fields.append({
                    "name": f"{col}_Nulls",
                    "expression": f"SUM(CASE WHEN [{col}] IS NULL THEN 1 ELSE 0 END)"
                })
        order_by = []
        limit = None
    elif any(kw in q_lower for kw in ["duplicate customer id", "duplicate customer ids", "duplicate ids", "duplicate records", "duplicates", "duplicate entries", "duplicate values", "which columns contain duplicate"]):
        intent = "duplicate_records"
        business_cols = [c for c in column_list if c.lower() not in ["recordid", "index", "sr"]]
        # Prioritize business fields that frequently have duplicates like contact, phone, product, name, cnic, email
        target_id_col = next((c for c in business_cols if any(kw in c.lower() for kw in ["contact", "phone", "product", "cnic", "name", "email", "address"])), business_cols[0] if business_cols else "Consignee_Contact")
        select_cols = [target_id_col]
        group_by = [target_id_col]
        computed_fields = [{
            "name": "Duplicate_Count",
            "expression": "COUNT(*)"
        }]
        order_by = [{"column": "Duplicate_Count", "sql_safe_column": "[Duplicate_Count]", "direction": "DESC"}]
        limit = 100
    elif "earliest" in q_lower and "latest" in q_lower and date_col:
        intent = "min_max_date"
        computed_fields.append({
            "name": "Earliest Subscription Date",
            "expression": f"MIN([{date_col}])"
        })
        computed_fields.append({
            "name": "Latest Subscription Date",
            "expression": f"MAX([{date_col}])"
        })
        select_cols = []
        order_by = []
    elif any(kw in q_lower for kw in ["percentage", "percent", "%", "ratio", "fraction"]):
        intent = "percentage_calc"
        filters = []  # Clear global filters so denominator COUNT(*) spans total dataset
        status_col = next((c for c in column_list if any(kw in c.lower() for kw in ["status", "delivery", "state"])), "Delivery_Status")
        if "return" in q_lower:
            computed_fields.append({"name": "Matching_Orders", "expression": f"COUNT(CASE WHEN [{status_col}] = 'Returned' OR [{status_col}] LIKE '%RETURN%' THEN 1 END)"})
            computed_fields.append({"name": "Total_Orders", "expression": "COUNT(*)"})
            computed_fields.append({"name": "Percentage", "expression": f"ROUND(CAST(COUNT(CASE WHEN [{status_col}] = 'Returned' OR [{status_col}] LIKE '%RETURN%' THEN 1 END) AS FLOAT) * 100.0 / NULLIF(COUNT(*), 0), 2)"})
        elif "deliver" in q_lower:
            computed_fields.append({"name": "Matching_Orders", "expression": f"COUNT(CASE WHEN [{status_col}] = 'Delivered' OR [{status_col}] LIKE '%DELIVER%' THEN 1 END)"})
            computed_fields.append({"name": "Total_Orders", "expression": "COUNT(*)"})
            computed_fields.append({"name": "Percentage", "expression": f"ROUND(CAST(COUNT(CASE WHEN [{status_col}] = 'Delivered' OR [{status_col}] LIKE '%DELIVER%' THEN 1 END) AS FLOAT) * 100.0 / NULLIF(COUNT(*), 0), 2)"})
        else:
            computed_fields.append({"name": "Matching_Orders", "expression": "COUNT(*)"})
            computed_fields.append({"name": "Total_Orders", "expression": "COUNT(*)"})
            computed_fields.append({"name": "Percentage", "expression": "100.0"})
        select_cols = []
        order_by = []
    # Check for Grouped Multi-Metric Aggregations & Comparison Queries (e.g. top 10 destinations by total COD value, compare statuses)
    categorical_cols = [c for c in column_list if any(kw in c.lower() for kw in ["destination", "origin", "status", "city", "country", "company", "category", "type", "state"])]
    numeric_candidate_cols = [c for c in column_list if c.lower() not in ["recordid", "index", "sr"] and any(kw in c.lower() for kw in ["cod", "val", "amount", "price", "cost", "weight", "total", "pieces", "quantity"])]
    
    group_col = None
    group_match = re.search(r"group\s*(?:them\s*)?by\s*([a-z_]+)", q_lower)
    if group_match:
        target_grp_word = group_match.group(1)
        group_col = next((c for c in categorical_cols if target_grp_word in c.lower() or c.lower() in target_grp_word), None)

    if not group_col:
        address_col = next((c for c in column_list if any(kw in c.lower() for kw in ["address", "street", "consignee_address", "location"])), None)
        if address_col and any(kw in q_lower for kw in ["area", "areas", "locality", "localities", "neighborhood", "neighborhoods", "sub-city", "gulshan", "saddar", "defence", "dha", "clifton", "johar", "korangi"]):
            from ai.area_intelligence import build_sql_sub_area_case_statement
            known_pk_cities = ["karachi", "lahore", "islamabad", "rawalpindi", "multan", "peshawar", "faisalabad", "quetta", "hyderabad", "sukkur", "sialkot", "gujranwala"]
            target_city_name = next((c.title() for c in known_pk_cities if c in q_lower), None)
            group_col = build_sql_sub_area_case_statement(address_col, target_city_name)
        elif any(kw in q_lower for kw in ["city", "cities", "shehar", "shahar", "destination", "destinations"]):
            group_col = next((c for c in column_list if any(kw in c.lower() for kw in ["destination", "city", "location"])), None)
        elif any(kw in q_lower for kw in ["origin", "origins"]):
            group_col = next((c for c in column_list if "origin" in c.lower()), None)
        elif any(kw in q_lower for kw in ["status", "statuses", "condition"]):
            group_col = next((c for c in column_list if any(kw in c.lower() for kw in ["status", "delivery"])), None)

    metric_col = next((c for c in numeric_candidate_cols if (c.lower() in q_lower or c.lower().replace("_", " ") in q_lower or (("cod" in q_lower or "value" in q_lower or "amount" in q_lower) and "cod" in c.lower()))), None)
    if not metric_col:
        metric_col = numeric_candidate_cols[0] if numeric_candidate_cols else None

    is_grouped_multi_metric = group_col and (
        any(kw in q_lower for kw in ["top ", "top", "highest", "most", "count by", "by count", "group by", "group them by", "compare", "for each", "per ", "destinations", "statuses", "cities", "origins", "breakdown", "kaun kaun", "zyaada", "zyada", "area", "areas", "locality"]) or
        ("by" in q_lower and ("total" in q_lower or "sum" in q_lower or "value" in q_lower or "cod" in q_lower or "amount" in q_lower or "city" in q_lower or "area" in q_lower))
    )

    if is_grouped_multi_metric:
        intent = "aggregate_rank"
        select_cols = [group_col]
        group_by = [group_col]
        computed_fields = []

        status_col = next((c for c in column_list if any(kw in c.lower() for kw in ["status", "delivery"])), None)
        if status_col and group_col != status_col and "return" in q_lower:
            if not any("return" in f["sql_clause"].lower() for f in filters):
                filters.append({
                    "column": status_col,
                    "sql_safe_column": f"[{status_col}]",
                    "operator": "STATUS_RETURN",
                    "value": "Returned",
                    "sql_clause": f"([{status_col}] = 'Returned' OR [{status_col}] LIKE '%RETURN%')"
                })
        
        safe_cast_expr = f"TRY_CAST(REPLACE(CAST([{metric_col}] AS NVARCHAR(MAX)), ',', '') AS FLOAT)" if metric_col else None

        # 1. Order Count
        if any(kw in q_lower for kw in ["count", "order count", "number of", "how many", "top", "compare", "returned-order count", "orders"]):
            computed_fields.append({"name": "Order_Count", "expression": "COUNT(*)"})

        # 2. Percentage of Total
        if any(kw in q_lower for kw in ["percentage", "percent", "%", "share", "ratio", "fraction"]):
            computed_fields.append({"name": "Percentage_Of_Total", "expression": f"ROUND(CAST(COUNT(*) AS FLOAT) * 100.0 / NULLIF((SELECT COUNT(*) FROM [{table_name}]), 0), 2)"})

        # 3. Total Sum / COD Value
        if safe_cast_expr and any(kw in q_lower for kw in ["total", "sum", "cod", "value", "amount", "revenue", "price", "cost"]):
            val_name = f"Total_{metric_col}" if metric_col else "Total_Value"
            computed_fields.append({"name": val_name, "expression": f"SUM({safe_cast_expr})"})

        # 4. Average per order
        if safe_cast_expr and any(kw in q_lower for kw in ["average", "avg", "per order", "mean"]):
            avg_name = f"Average_{metric_col}_Per_Order" if metric_col else "Average_Value"
            computed_fields.append({"name": avg_name, "expression": f"ROUND(AVG({safe_cast_expr}), 2)"})

        if not computed_fields:
            computed_fields.append({"name": "Order_Count", "expression": "COUNT(*)"})

        # Primary Sort Column
        sort_field = computed_fields[0]["name"]
        for f in computed_fields:
            if "total" in f["name"].lower() or "sum" in f["name"].lower() or "value" in f["name"].lower():
                if "total" in q_lower or "by total" in q_lower or "value" in q_lower:
                    sort_field = f["name"]
                    break

        order_by = [{"column": sort_field, "sql_safe_column": f"[{sort_field}]", "direction": "DESC"}]

        if not limit:
            limit_m = re.search(r"\b(?:top|first)\s*(\d{1,3})\b", q_lower)
            limit = int(limit_m.group(1)) if limit_m else (10 if "top" in q_lower else None)

    elif any(kw in q_lower for kw in ["total cod", "cod value", "total amount", "total value", "total revenue", "total price", "total cost", "total sum", "sum of", "average", "avg", "mean", "minimum", "min", "maximum", "max", "distinct"]):
        intent = "aggregate_stat"
        num_col = metric_col if metric_col else column_list[0]
        safe_cast_expr = f"TRY_CAST(REPLACE(CAST([{num_col}] AS NVARCHAR(MAX)), ',', '') AS FLOAT)"

        if "distinct" in q_lower:
            dist_col = next((c for c in column_list if c.lower() in q_lower and c.lower() not in ["recordid", "index", "sr"]), num_col)
            computed_fields.append({"name": f"Distinct_{dist_col}_Count", "expression": f"COUNT(DISTINCT [{dist_col}])"})
        elif any(kw in q_lower for kw in ["average", "avg", "mean"]):
            computed_fields.append({"name": f"Average_{num_col}", "expression": f"ROUND(AVG({safe_cast_expr}), 2)"})
        elif any(kw in q_lower for kw in ["minimum", "min"]):
            computed_fields.append({"name": f"Minimum_{num_col}", "expression": f"MIN({safe_cast_expr})"})
        elif any(kw in q_lower for kw in ["maximum", "max"]):
            computed_fields.append({"name": f"Maximum_{num_col}", "expression": f"MAX({safe_cast_expr})"})
        else:
            computed_fields.append({"name": f"Total_{num_col}", "expression": f"SUM({safe_cast_expr})"})

        select_cols = []
        order_by = []
    else:
        # Detect category grouping/ranking intent (e.g. "top 5 countries by customer count", "top 10 cities", "count by country")
        category_match_col = group_col
        if not category_match_col:
            category_match_col = next((c for c in column_list if (c.lower() in q_lower or c.lower().replace("_", " ") in q_lower) and c.lower() not in ["recordid", "index"]), None)
        if not category_match_col:
            if "country" in q_lower or "countries" in q_lower:
                category_match_col = next((c for c in column_list if "country" in c.lower()), None)
            elif "city" in q_lower or "cities" in q_lower:
                category_match_col = next((c for c in column_list if "city" in c.lower()), None)
            elif "company" in q_lower or "companies" in q_lower:
                category_match_col = next((c for c in column_list if "company" in c.lower()), None)

        is_category_ranking = category_match_col and (
            any(kw in q_lower for kw in ["top ", "top", "highest", "most", "count by", "by count", "by customer count", "group by", "per country", "per city", "per company", "countries", "cities", "companies"]) or
            ("by" in q_lower and "count" in q_lower)
        )

        if is_category_ranking:
            intent = "aggregate_rank"
            select_cols = [category_match_col]
            group_by = [category_match_col]
            computed_fields = [{
                "name": "Total Customers",
                "expression": "COUNT(*)"
            }]
            order_by = [{"column": "Total Customers", "sql_safe_column": "[Total Customers]", "direction": "DESC"}]
            if not limit:
                limit_m = re.search(r"\b(?:top|first)\s*(\d{1,3})\b", q_lower)
                limit = int(limit_m.group(1)) if limit_m else 5
        elif any(kw in q_lower for kw in ["how many", "count", "total customers", "number of"]) and not filters:
            intent = "count"

    return {
        "intent": intent,
        "question": question,
        "table_name": table_name,
        "select": select_cols,
        "computed_fields": computed_fields,
        "filters": filters,
        "group_by": group_by,
        "order_by": order_by,
        "limit": limit,
        "concept_validation": concept_check
    }
