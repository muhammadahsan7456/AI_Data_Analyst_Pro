"""
Semantic Column & Schema Understanding Service
Maps natural language user prompt phrases to real database columns using:
1. Exact column name match
2. Normalized token match
3. Domain synonym dictionary mapping
4. Distinct value content validation
5. Fuzzy ratio matching
Ensures ZERO hallucinated column names in generated T-SQL.
"""

import re
from difflib import SequenceMatcher
from database.connection import run_query, sanitize_identifier

SYNONYM_MAP = {
    "returned": ["delivery_status", "status_description", "order_status", "status", "return_status", "returned"],
    "return": ["delivery_status", "status_description", "order_status", "status", "return_status", "returned"],
    "delivered": ["delivery_status", "status_description", "order_status", "status"],
    "city": ["city", "customer_city", "shipping_city", "dest_city", "destination_city", "location", "region"],
    "area": ["area", "customer_area", "district", "region", "address_area", "consignee_address", "address"],
    "customer": ["customer_name", "consignee_name", "customer", "client", "name", "buyer", "customer_id"],
    "price": ["amount", "price", "total_amount", "sales", "revenue", "order_value", "cod_amount"],
    "sales": ["amount", "price", "total_amount", "sales", "revenue", "order_value", "cod_amount"],
    "revenue": ["amount", "price", "total_amount", "sales", "revenue", "order_value", "cod_amount"],
    "amount": ["amount", "price", "total_amount", "sales", "revenue", "order_value", "cod_amount"],
    "status": ["status_description", "delivery_status", "order_status", "status", "state"],
    "date": ["booking_date", "order_date", "date", "created_at", "delivery_date", "shipment_date"],
    "product": ["item_description", "product_name", "item", "product", "sku", "category"]
}


def normalize_token(s: str) -> str:
    return re.sub(r"[^\w]", "", str(s).lower().strip())


def find_best_matching_column(user_term: str, columns_list: list) -> str:
    """
    Intelligently map user term (e.g. "returned", "city", "area") to real column name.
    """
    if not user_term or not columns_list:
        return ""

    norm_term = normalize_token(user_term)
    cols_clean = {normalize_token(c): c for c in columns_list}

    # 1. Exact or normalized match
    if norm_term in cols_clean:
        return cols_clean[norm_term]

    # 2. Synonym dictionary lookup
    for syn_key, target_candidates in SYNONYM_MAP.items():
        if syn_key == norm_term or syn_key in norm_term or norm_term in syn_key:
            for candidate in target_candidates:
                cand_norm = normalize_token(candidate)
                for col_norm, original_col in cols_clean.items():
                    if cand_norm == col_norm or cand_norm in col_norm or col_norm in cand_norm:
                        return original_col

    # 3. Fuzzy similarity score
    best_col = ""
    best_score = 0.0
    for col in columns_list:
        score = SequenceMatcher(None, norm_term, normalize_token(col)).ratio()
        if score > best_score and score >= 0.65:
            best_score = score
            best_col = col

    return best_col


def resolve_query_semantic_columns(question: str, table_name: str, columns_list: list) -> dict:
    """
    Scans question for semantic entities and resolves target columns & filter conditions.
    Returns:
        {
            "resolved_columns": ["Delivery_Status", "City", "Area"],
            "status_col": "Delivery_Status",
            "city_col": "City",
            "area_col": "Area",
            "filter_conditions": [
                {"col": "Delivery_Status", "val": "Returned"},
                {"col": "City", "val": "Karachi"}
            ]
        }
    """
    q_lower = question.lower()
    resolved = {
        "resolved_columns": [],
        "status_col": find_best_matching_column("status", columns_list),
        "city_col": find_best_matching_column("city", columns_list),
        "area_col": find_best_matching_column("area", columns_list),
        "amount_col": find_best_matching_column("amount", columns_list),
        "date_col": find_best_matching_column("date", columns_list),
        "filter_conditions": []
    }

    # Check for Return/Returned intent
    if "return" in q_lower or "returned" in q_lower or "rto" in q_lower:
        if resolved["status_col"]:
            resolved["filter_conditions"].append({
                "col": resolved["status_col"],
                "val": "Returned"
            })
            if resolved["status_col"] not in resolved["resolved_columns"]:
                resolved["resolved_columns"].append(resolved["status_col"])

    # Check for Delivered intent
    if "deliver" in q_lower or "delivered" in q_lower:
        if resolved["status_col"]:
            resolved["filter_conditions"].append({
                "col": resolved["status_col"],
                "val": "Delivered"
            })
            if resolved["status_col"] not in resolved["resolved_columns"]:
                resolved["resolved_columns"].append(resolved["status_col"])

    # Scan for city mentions in distinct column values
    if resolved["city_col"]:
        safe_tbl = sanitize_identifier(table_name)
        safe_city = sanitize_identifier(resolved["city_col"])
        try:
            cities_df = run_query(f"SELECT DISTINCT TOP 50 {safe_city} FROM {safe_tbl} WHERE {safe_city} IS NOT NULL;")
            if not cities_df.empty:
                city_list = cities_df.iloc[:, 0].dropna().astype(str).tolist()
                for c_val in city_list:
                    if c_val.lower().strip() in q_lower and len(c_val.strip()) >= 3:
                        resolved["filter_conditions"].append({
                            "col": resolved["city_col"],
                            "val": c_val
                        })
                        if resolved["city_col"] not in resolved["resolved_columns"]:
                            resolved["resolved_columns"].append(resolved["city_col"])
                        break
        except Exception:
            pass

    return resolved
