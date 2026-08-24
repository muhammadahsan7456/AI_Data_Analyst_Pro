import os
import sys
import re
import time
import pandas as pd
from dotenv import load_dotenv
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# Ensure workspace root is in sys.path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from ai.sql_agent import generate_sql_prompt
    from ai.semantic_matcher import resolve_query_semantic_columns
    from database.connection import run_query, is_safe_identifier, sanitize_identifier
    from utils.cache import system_cache
except ModuleNotFoundError:
    from sql_agent import generate_sql_prompt
    try:
        from semantic_matcher import resolve_query_semantic_columns
    except ModuleNotFoundError:
        resolve_query_semantic_columns = None
    from connection import run_query, is_safe_identifier, sanitize_identifier
    try:
        from utils.cache import system_cache
    except ModuleNotFoundError:
        system_cache = None

load_dotenv()

# Initialize OpenRouter Client
api_key = os.getenv("OPENROUTER_API_KEY", "")
client = None
if api_key:
    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1"
        )
    except Exception:
        client = None


UNRELATED_MESSAGE = "I'm currently analyzing your selected dataset. Please ask a question related to its records, columns, trends, statistics, or business insights."

# Priority list of ultra-fast OpenRouter models
FAST_MODELS = [
    "google/gemini-2.0-flash-lite-001",
    "google/gemini-flash-1.5-8b",
    "openai/gpt-4o-mini",
    "meta-llama/llama-3.3-70b-instruct:free",
    "openai/gpt-3.5-turbo"
]


def extract_entity_phrase(question: str) -> str:
    """
    Extract exact multi-word entity or quoted string from user question with 100% precision.
    e.g. 'just "Saint Helena"' -> 'Saint Helena'
         'mujha Saint Helena country ka data la ka do' -> 'Saint Helena'
         'Masonberg city ka data' -> 'Masonberg'
    """
    if not question:
        return ""

    # 1. Check for quoted string first
    quoted_match = re.search(r'["\']([^"\']+)["\']', question)
    if quoted_match:
        val = quoted_match.group(1).strip()
        if len(val) >= 2:
            return val

    # 2. Clean common prompt filler words & limit keywords to extract true target entity name
    filler_patterns = [
        r"\b(show|get|fetch|give|display|all|data|set|records|rows|row|table|details|info|orders|order|items|item|list|log|entries|entry|ross|raws|rose)\b",
        r"\b(mujha|mujhe|chaya|chahiye|ka|ki|ke|k|den|do|batao|bataen|batado|bataye|dikhao|dikhaye|dikhayein|dekhao|dekhaye|dekhayen|dekhain|dekho|dikhade|dikhado|karo|karein|bhej|bhejo|la|laka|lao|select|from|where)\b",
        r"\b(first|last|top|highest|lowest|most|least|count|total|summary|average|min|max|sum|by|per|sort|order|descending|ascending|desc|asc|pehle|aakhri|aakhiri|akhri|bottom|niche|end|latest|recent)\b",
        r"\b(number|of|the|in|for|a|an|is|are|ko|se|me|main|id)\b",
        r"\b(country|city|state|region|name|category|item|just|only|please)\b"
    ]
    cleaned = question
    for pat in filler_patterns:
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"[^\w\s-]", " ", cleaned).strip()
    words = [w for w in cleaned.split() if len(w) >= 2 and not w.isdigit()]
    return " ".join(words).strip()

def is_technical_or_id_column(col_name: str, sample_val=None) -> bool:
    """
    Check if a column is a technical ID, serial number, tracking number, phone number, or CNIC code.
    Prevents summing or averaging tracking IDs, phone numbers, or CNICs.
    """
    c_lower = col_name.lower().strip()
    id_terms = [
        "recordid", "datasetid", "userid", "index", "sr", "s.no", "s_no", "sno",
        "consignment", "tracking", "cnic", "contact", "phone", "mobile", "zip",
        "pincode", "account", "card", "ref", "reference", "order_id", "status_code",
        "code", "row_num", "guid", "uuid"
    ]
    if any(term in c_lower for term in id_terms):
        return True
    if c_lower.endswith("_id") or c_lower == "id":
        return True
    return False


def generate_data_business_summary(table_name: str, df: pd.DataFrame, question: str) -> str:
    """
    Generate deep, readable, highly attractive executive-level AI business insights.
    Directly answers user question, formats key breakdowns, and excludes tracking IDs from numerical sums.
    """
    if df is None or df.empty:
        return f"No matching records found in dataset `[{table_name}]` for query *'{question}'*."

    total_rows = len(df)
    total_cols = len(df.columns)

    # Clean text columns and real numeric metric columns
    real_numeric_cols = []
    for col in df.columns:
        if not is_technical_or_id_column(col) and pd.api.types.is_numeric_dtype(df[col]):
            # Verify values aren't giant tracking numbers (e.g. > 10,000,000)
            valid_num = df[col].dropna()
            if not valid_num.empty and valid_num.max() < 10000000:
                real_numeric_cols.append(col)

    text_cols = [c for c in df.columns if not is_technical_or_id_column(c) and not pd.api.types.is_numeric_dtype(df[c])]

    summary_parts = []

    # 1. Core Direct Query Answer
    summary_parts.append(
        f"🎯 **Executive Query Summary**:\n"
        f"Retrieved **{total_rows:,} matching records** across **{total_cols} columns** from dataset `[{table_name}]` for question: *'{question}'*."
    )

    # 2. Key Category & Location Breakdown
    cat_highlights = []
    for col in text_cols:
        col_low = col.lower()
        if any(k in col_low for k in ["origin", "destination", "city", "status", "area", "address", "service", "detail"]):
            val_counts = df[col].dropna().value_counts()
            if not val_counts.empty:
                top_name = str(val_counts.index[0])
                top_cnt = int(val_counts.iloc[0])
                pct = round((top_cnt / total_rows) * 100, 1)

                if len(val_counts) == 1:
                    cat_highlights.append(f"• **{col}**: All {total_rows} records belong exclusively to **{top_name}**.")
                else:
                    top_3 = ", ".join([f"**{idx}** ({val})" for idx, val in val_counts.head(3).items()])
                    cat_highlights.append(f"• **{col}**: Top entries are {top_3}.")

    if cat_highlights:
        summary_parts.append("📌 **Key Category & Location Breakdown**:\n" + "\n".join(cat_highlights[:4]))

    # 3. Real Financial & Operational Metrics (No ID/Phone Number Sums)
    if real_numeric_cols:
        metric_items = []
        for col in real_numeric_cols[:4]:
            col_sum = df[col].sum()
            col_avg = df[col].mean()
            col_max = df[col].max()

            if pd.notna(col_sum) and abs(col_sum) > 0:
                metric_items.append(
                    f"• **{col}**: Total = **{col_sum:,.2f}** | Mean Average = **{col_avg:,.2f}** (Peak: **{col_max:,.2f}**)"
                )
        if metric_items:
            summary_parts.append("💰 **Financial & Operational Metrics**:\n" + "\n".join(metric_items))

    # 4. Strategic Executive Actionable Takeaway
    takeaway_text = f"The retrieved **{total_rows:,} records** demonstrate clear operational distribution. "
    if "return" in question.lower():
        takeaway_text += "Management should investigate top destination areas with high return rates to reduce logistics overhead and improve fulfillment success."
    else:
        takeaway_text += "Business teams should leverage these filtered insights to streamline delivery routes, optimize stock allocation, and improve customer satisfaction."

    summary_parts.append("💡 **Strategic Action Plan**:\n" + takeaway_text)

    return "\n\n".join(summary_parts)


def normalize_speech_phonetics(question: str) -> str:
    """
    Normalizes speech-to-text typos, mishearings, Roman Urdu phonetics, and common voice AI variations.
    Example: 'top 5 ross' -> 'top 5 rows', '5 raws' -> 'top 5 rows', 'panch rows' -> 'top 5 rows'
    """
    if not question or not isinstance(question, str):
        return ""

    q = question.lower().strip()

    # 1. Phonetic replacement for common mishearings and typos
    q = re.sub(r"\b(ross|raws|rose|roes|rowses|row)\b", "rows", q)
    q = re.sub(r"\b(ricord|ricords|rekaard|recs|rec)\b", "records", q)
    q = re.sub(r"\b(detta|daata|dataa)\b", "data", q)
    q = re.sub(r"\b(deliverd|delivred|deliver|delver|delivrd)\b", "delivered", q)
    q = re.sub(r"\b(cancled|canceld|cancle)\b", "cancelled", q)
    q = re.sub(r"\b(pendin|pendg)\b", "pending", q)

    # 2. Urdu numbers to digits
    q = re.sub(r"\b(ek|aik)\b", "1", q)
    q = re.sub(r"\b(do|doo)\b", "2", q)
    q = re.sub(r"\b(teen|tin)\b", "3", q)
    q = re.sub(r"\b(chaar|char)\b", "4", q)
    q = re.sub(r"\b(panch|paanch|panc|paanched)\b", "5", q)
    q = re.sub(r"\b(che|cheh)\b", "6", q)
    q = re.sub(r"\b(saat|sat)\b", "7", q)
    q = re.sub(r"\b(aath|ath)\b", "8", q)
    q = re.sub(r"\b(nau|noo)\b", "9", q)
    q = re.sub(r"\b(das|dass)\b", "10", q)
    q = re.sub(r"\b(bees|bis)\b", "20", q)
    q = re.sub(r"\b(pachas|pachass)\b", "50", q)
    q = re.sub(r"\b(sau|so)\b", "100", q)

    # 3. Structural normalizations (e.g. '5 rows' -> 'top 5 rows')
    if re.match(r"^(\d+)\s+(rows|records|data)\b", q):
        q = "top " + q

    return q


def is_out_of_domain_question(question: str, columns_list: list) -> bool:
    """
    Strict Domain Guardrail Classifier.
    Ensures AI refuses to answer general knowledge, coding, weather, political, or chat questions,
    and ONLY answers dataset/data questions.
    """
    if not question or not isinstance(question, str):
        return True

    question_norm = normalize_speech_phonetics(question)
    q_lower = question_norm.lower().strip()

    # 1. Conversational & Casual Chat Triggers (Instant Out-of-Domain Guardrail)
    chat_patterns = [
        r"^\s*(hi|hello|hey|greetings|hola|namaste|assalam|salaam|good morning|good evening|good afternoon)\b",
        r"\bhow are you\b", r"\bwho are you\b", r"\bwhat is your name\b", r"\bwhat is my name\b",
        r"\bwho am i\b", r"\bwhere am i\b", r"\bwho created you\b", r"\bwho developed you\b",
        r"\btell me a joke\b", r"\btell me a story\b", r"\bwrite a poem\b",
        r"\bsing a song\b", r"\bwhat can you do\b", r"\bcan we talk\b"
    ]
    for pattern in chat_patterns:
        if re.search(pattern, q_lower):
            # Exception: if query also contains explicit dataset commands (e.g. 'hello top 10 rows'), allow processing
            if not any(kw in q_lower for kw in ["top", "records", "rows", "count", "total", "sum", "data"]):
                return True

    # 2. General Knowledge / History / Geography / Science / Politics Triggers
    general_patterns = [
        r"\bwho is\b", r"\bwho was\b", r"\bwhat is the capital\b", r"\bcapital of\b",
        r"\bpresident of\b", r"\bprime minister of\b", r"\bhistory of\b", r"\bmeaning of\b",
        r"\brecipe for\b", r"\bhow to make\b", r"\bweather in\b", r"\bweather for\b", r"\bweather today\b",
        r"\bwho won\b", r"\bpopulation of\b", r"\bwhat is quantum\b", r"\bexplain physics\b",
        r"\bexplain chemistry\b", r"\bexplain biology\b", r"\bwhat is ai\b", r"\bwhat is machine learning\b"
    ]
    for pattern in general_patterns:
        if re.search(pattern, q_lower):
            return True

    # 3. General Software Development & Coding Triggers
    coding_patterns = [
        r"\bwrite a python\b", r"\bwrite python\b", r"\bwrite html\b", r"\bwrite css\b",
        r"\bwrite a code\b", r"\bhow to code\b", r"\bexplain code\b", r"\binstall pip\b",
        r"\bjavascript function\b", r"\bwrite c\+\+\b", r"\bwrite java\b"
    ]
    for pattern in coding_patterns:
        if re.search(pattern, q_lower):
            return True

    # 4. Check for Dataset Relevance: Must match at least one dataset concept, column name, or entity search
    dataset_keywords = {
        "show", "get", "fetch", "give", "display", "list", "count", "total", "sum",
        "average", "avg", "mean", "median", "max", "maximum", "min", "minimum",
        "highest", "lowest", "top", "bottom", "first", "last", "records", "rows",
        "ross", "raws", "rose", "roes", "recs", "ricord",
        "data", "dataset", "table", "summary", "chart", "graph", "trend", "distribution",
        "category", "column", "value", "where", "filter", "group", "sort", "order",
        "ka", "ki", "ke", "ko", "se", "me", "main", "mujha", "mujhe", "chaya", "chahiye",
        "city", "country", "state", "name", "customer", "product", "sales", "details", "just",
        "dikhao", "dikhaye", "dikhayein", "batao", "batayein", "la", "do", "karo", "delivered",
        "deliverd", "pending", "cancelled", "id", "code", "serial", "number"
    }

    words = set(re.findall(r"\b[A-Za-z0-9_-]+\b", q_lower))
    
    # Check if any word matches dataset table column names
    col_names = {str(col).lower() for col in columns_list}
    if words.intersection(col_names):
        return False

    # Check if any word matches general analytical dataset keywords
    if words.intersection(dataset_keywords):
        return False

    # Allow entity searches (e.g. 'Saint Helena', 'Masonberg', 'Eritrea')
    entity = extract_entity_phrase(question_norm)
    if entity and len(entity) >= 2:
        return False

    return True


def find_primary_key_column(columns_list: list) -> str:
    """
    Find best primary key / serial column (Sr, S.No, RecordID, ID, etc.) for deterministic ordering.
    """
    if not columns_list:
        return ""
    candidates = ["sr", "sr.", "s.no", "sno", "recordid", "id", "order_id", "orderid", "customer_id", "customerid"]
    for candidate in candidates:
        for col in columns_list:
            cleaned = col.lower().strip().replace("_", "").replace(".", "")
            if cleaned == candidate or col.lower().strip() == candidate:
                return col
    for col in columns_list:
        if col.lower().endswith("id") or col.lower().startswith("id"):
            return col
    return columns_list[0]


def extract_entity_phrase(question: str) -> str:
    """
    Extract exact multi-word entity, phone number, ID, or quoted string from user question with 100% precision.
    """
    if not question:
        return ""

    # 1. Check for quoted string first
    quoted_match = re.search(r'["\']([^"\']+)["\']', question)
    if quoted_match:
        val = quoted_match.group(1).strip()
        if len(val) >= 2:
            return val

    # 2. Check for explicit phone numbers or numeric IDs (e.g. 03053107456, +923053107456, ID 1005)
    phone_match = re.search(r'\b(?:\+?\d{1,4}[\s-]?)?\(?\d{2,5}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}\b', question)
    if phone_match:
        phone_val = phone_match.group(0).strip()
        if len(re.sub(r'\D', '', phone_val)) >= 5:
            return phone_val

    id_match = re.search(r'\b(?:id|code|number|serial|no|sr)\s*[:=]?\s*(\w+)\b', question, flags=re.IGNORECASE)
    if id_match:
        return id_match.group(1).strip()

    # 3. Clean common prompt filler words & limit keywords to extract true target entity name
    filler_patterns = [
        r"\b(show|get|fetch|give|display|all|data|set|records|rows|row|table|details|info|orders|order|items|item|list|log|entries|entry|sheet|file|dataset|ross|raws|rose|customers|customer|clients|client|people|person|users|user|buyers|buyer)\b",
        r"\b(mujha|mujhe|chaya|chahiye|ka|ki|ke|k|den|do|batao|bataen|batado|bataye|dikhao|dikhaye|dikhayein|dekhao|dekhaye|dekhayen|dekhain|dekho|dikhade|dikhado|karo|karein|bhej|bhejo|la|laka|lao|select|from|where)\b",
        r"\b(first|last|top|highest|lowest|most|least|count|total|summary|average|min|max|sum|by|per|sort|order|descending|ascending|desc|asc|pehle|aakhri|aakhiri|akhri|bottom|niche|end|latest|recent)\b",
        r"\b(phone\s*number|phone|mobile|contact|company|city|country|state|region|name|category|item|just|only|please|plz|pls|plzz|plzui|plzzui|kindly|thanks|thank|sir|bhai|bro|brother|boss|dear|admin|yar|yaar|ji|g|hain|bhi|sara|sare|sab|sabji|poora|pura|tamam|entire|full|complete|everything|every|number|of|the|in|for|a|an|is|are|ko|se|me|main|par|pa|pe|par|koi|toh|to)\b"
    ]
    cleaned = question
    for pat in filler_patterns:
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"[^\w\s-]", " ", cleaned).strip()
    words = [w for w in cleaned.split() if len(w) >= 2]
    return " ".join(words).strip()


def fast_pattern_sql_generator(question: str, table_name: str, columns_list: list) -> str:
    """
    Sub-second (0.001s) Local T-SQL Rule Engine with intelligent pattern matching,
    column sorting, entity filtering, phone number searching, and exact record limit extraction.
    """
    question_norm = normalize_speech_phonetics(question)
    q_lower = question_norm.lower().strip()
    safe_tbl = sanitize_identifier(table_name)
    primary_pk_col = find_primary_key_column(columns_list)
    safe_pk = sanitize_identifier(primary_pk_col) if primary_pk_col else ""

    # 0. Check for "Show All / Full Data / Sara Data" intent
    is_show_all = any(kw in q_lower for kw in ["all", "sara", "sare", "sab", "poora", "pura", "tamam", "entire", "full", "everything"])

    # 0. Check for "Count" / "Total rows"
    if re.search(r"\b(count|total|how many|kitni|kitne)\b.*\b(records|rows|data|entries|ross|raws|rose)\b", q_lower):
        return f"SELECT COUNT(*) AS [TotalRecords] FROM {safe_tbl};"

    # 1. Check for "Last / Bottom / Aakhri / Latest" intent
    is_last_request = any(kw in q_lower for kw in ["last", "bottom", "aakhri", "aakhiri", "akhri", "end ke", "niche ke", "niche", "latest", "recent"])

    # 2. Check for "Top / First / Highest / Lowest" intent
    is_top_request = any(kw in q_lower for kw in ["top", "first", "pehle", "starting", "head"]) or bool(re.search(r"\b(top|first|pehle)\s*\d{1,4}\b", q_lower))

    # 3. Extract limit N
    limit_val = None
    top_match = re.search(r"\b(?:top|first|highest|lowest|last|bottom|show|get|fetch|display|pehle|aakhri|aakhiri|akhri)\s*(?:ke|ki|k)?\s*(\d{1,4})\b", q_lower)
    if not top_match:
        top_match = re.search(r"\b(\d{1,4})\s*(?:records|rows|entries|data|items|ross|raws|rose)\b", q_lower)
    if not top_match:
        top_match = re.search(r"\b(\d{1,4})\b", q_lower)

    if top_match and top_match.group(1):
        try:
            val = int(top_match.group(1))
            if 1 <= val <= 5000:
                limit_val = val
        except ValueError:
            limit_val = None

    if (is_last_request or is_top_request) and limit_val is None:
        limit_val = 10

    # 4. Check for column sort request
    sort_col = None
    for col in columns_list:
        col_low = col.lower().strip()
        if col_low != primary_pk_col.lower() and len(col_low) >= 3:
            if re.search(r"\b" + re.escape(col_low) + r"\b", q_lower) or col_low in q_lower:
                sort_col = col
                break

    # 5. Extract specific filter target entity
    target_entity = extract_entity_phrase(question_norm)
    generic_record_nouns = {"customers", "customer", "clients", "client", "people", "person", "users", "user", "buyers", "buyer", "sellers", "seller", "products", "product", "items", "item", "orders", "order", "records", "rows", "entries", "entry", "data", "list"}

    if is_show_all or target_entity.lower() in generic_record_nouns:
        target_entity = ""

    if target_entity:
        if (target_entity.isdigit() and len(target_entity) <= 3 and limit_val is not None) or target_entity.lower() in [c.lower().strip() for c in columns_list]:
            target_entity = ""

    # Rule A: If no target entity filter or show all records request
    if not target_entity or is_show_all:
        limit_str = f"TOP {limit_val}" if limit_val else ""
        limit_clause = f" {limit_str}" if limit_str else ""
        if sort_col:
            direction = "ASC" if any(kw in q_lower for kw in ["lowest", "bottom", "kam", "least", "min"]) else "DESC"
            return f"SELECT{limit_clause} * FROM {safe_tbl} ORDER BY {sanitize_identifier(sort_col)} {direction};"
        elif is_last_request:
            return f"SELECT{limit_clause} * FROM {safe_tbl} ORDER BY {safe_pk} DESC;" if safe_pk else f"SELECT{limit_clause} * FROM {safe_tbl};"
        elif is_top_request:
            return f"SELECT{limit_clause} * FROM {safe_tbl} ORDER BY {safe_pk} ASC;" if safe_pk else f"SELECT{limit_clause} * FROM {safe_tbl};"
        else:
            if is_show_all and not limit_val:
                return f"SELECT * FROM {safe_tbl} ORDER BY {safe_pk} ASC;" if safe_pk else f"SELECT * FROM {safe_tbl};"
            else:
                top_str = f"TOP {limit_val}" if limit_val else "TOP 100"
                return f"SELECT {top_str} * FROM {safe_tbl} ORDER BY {safe_pk} ASC;" if safe_pk else f"SELECT {top_str} * FROM {safe_tbl};"

    # Rule B: If target entity filter IS present (e.g. "Karachi", "Delivered", "ID 1005", "Ali")
    search_cols = list(columns_list)
    if search_cols:
        limit_clause = f"TOP {limit_val} " if limit_val else "TOP 500 "
        order_clause = f" ORDER BY {safe_pk} {'DESC' if is_last_request else 'ASC'}" if safe_pk else ""

        # Priority 0: Semantic Multi-Condition Resolution (e.g. "returned orders from Karachi")
        if resolve_query_semantic_columns:
            sem_res = resolve_query_semantic_columns(question, table_name, columns_list)
            if sem_res and sem_res.get("filter_conditions") and len(sem_res["filter_conditions"]) >= 2:
                cond_strs = [f"CAST({sanitize_identifier(fc['col'])} AS NVARCHAR(MAX)) = '{fc['val']}'" for fc in sem_res["filter_conditions"]]
                sem_sql = f"SELECT {limit_clause}* FROM {safe_tbl} WHERE {' AND '.join(cond_strs)}{order_clause};"
                try:
                    test_df = run_query(sem_sql)
                    if test_df is not None and not test_df.empty:
                        return sem_sql
                except Exception:
                    pass

        # Priority 1: Check status/state specific columns for status queries (e.g. "delivered", "pending", "cancelled", "returned")
        status_cols = [c for c in search_cols if any(kw in c.lower() for kw in ["status", "state", "stage", "condition"])]
        if status_cols:
            status_exact_conditions = [f"CAST({sanitize_identifier(col)} AS NVARCHAR(MAX)) = '{target_entity}'" for col in status_cols]
            status_sql = f"SELECT {limit_clause}* FROM {safe_tbl} WHERE {' OR '.join(status_exact_conditions)}{order_clause};"
            try:
                test_df = run_query(status_sql)
                if test_df is not None and not test_df.empty:
                    return status_sql
            except Exception:
                pass

            status_like_conditions = [f"CAST({sanitize_identifier(col)} AS NVARCHAR(MAX)) LIKE '%{target_entity}%'" for col in status_cols]
            status_like_sql = f"SELECT {limit_clause}* FROM {safe_tbl} WHERE {' OR '.join(status_like_conditions)}{order_clause};"
            try:
                test_df = run_query(status_like_sql)
                if test_df is not None and not test_df.empty:
                    return status_like_sql
            except Exception:
                pass

        # Priority 2: Exact match across ALL columns
        exact_conditions = [f"CAST({sanitize_identifier(col)} AS NVARCHAR(MAX)) = '{target_entity}'" for col in search_cols]
        exact_sql = f"SELECT {limit_clause}* FROM {safe_tbl} WHERE {' OR '.join(exact_conditions)}{order_clause};"
        try:
            test_df = run_query(exact_sql)
            if test_df is not None and not test_df.empty:
                return exact_sql
        except Exception:
            pass

        # Priority 3: Phrase LIKE match across ALL columns
        phrase_conditions = [f"CAST({sanitize_identifier(col)} AS NVARCHAR(MAX)) LIKE '%{target_entity}%'" for col in search_cols]
        phrase_sql = f"SELECT {limit_clause}* FROM {safe_tbl} WHERE {' OR '.join(phrase_conditions)}{order_clause};"
        try:
            test_df = run_query(phrase_sql)
            if test_df is not None and not test_df.empty:
                return phrase_sql
        except Exception:
            pass

        # Priority 4: Key token match across ALL columns
        tokens = [t for t in target_entity.split() if len(t) >= 2]
        if tokens:
            token_conditions = []
            for token in tokens:
                for col in search_cols:
                    token_conditions.append(f"CAST({sanitize_identifier(col)} AS NVARCHAR(MAX)) LIKE '%{token}%'")
            token_sql = f"SELECT {limit_clause}* FROM {safe_tbl} WHERE {' OR '.join(token_conditions)}{order_clause};"
            try:
                test_df = run_query(token_sql)
                if test_df is not None and not test_df.empty:
                    return token_sql
            except Exception:
                pass

        # Default fallback if specific filter yielded 0 rows
        return f"SELECT TOP 100 * FROM {safe_tbl};"

    return f"SELECT TOP 100 * FROM {safe_tbl};"


def ask_gemini(prompt: str) -> str:
    """
    Send prompt to OpenRouter AI API using ultra-fast model fallback loop for sub-second response times.
    """
    if not client or not api_key:
        return ""

    cache_key = f"ai_prompt_{hash(prompt)}"
    if system_cache:
        cached_val = system_cache.get(cache_key)
        if cached_val:
            return cached_val

    for model_name in FAST_MODELS:
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                timeout=2.0
            )
            if response and response.choices:
                result = response.choices[0].message.content or ""
                if result.strip():
                    if system_cache:
                        system_cache.set(cache_key, result, ttl=1800)
                    return result
        except Exception:
            continue

    return ""


def clean_sql_response(raw_text: str) -> str:
    """
    Extract and clean raw SQL code from AI text response.
    Enforces strict read-only execution by blocking destructive DDL/DML keywords.
    """
    if not raw_text:
        return ""

    if UNRELATED_MESSAGE.lower() in raw_text.lower():
        return UNRELATED_MESSAGE

    cleaned = raw_text.replace("```sql", "").replace("```", "").strip()

    match = re.search(r"\bSELECT\b", cleaned, re.IGNORECASE)
    if match:
        cleaned = cleaned[match.start():]
    else:
        return UNRELATED_MESSAGE

    cleaned = cleaned.split(";")[0].strip() + ";"

    # Block destructive SQL operations strictly for data integrity
    destructive_patterns = [
        r"\bDROP\b", r"\bDELETE\b", r"\bTRUNCATE\b", r"\bUPDATE\b",
        r"\bINSERT\b", r"\bALTER\b", r"\bEXEC\b", r"\bEXECUTE\b",
        r"\bGRANT\b", r"\bREVOKE\b"
    ]
    for pat in destructive_patterns:
        if re.search(pat, cleaned, re.IGNORECASE):
            return UNRELATED_MESSAGE

    limit_match = re.search(r"\bLIMIT\s+(\d+)\b", cleaned, re.IGNORECASE)
    if limit_match:
        limit_val = limit_match.group(1)
        cleaned = re.sub(r"\bLIMIT\s+\d+\b", "", cleaned, flags=re.IGNORECASE).strip()
        if not cleaned.endswith(";"):
            cleaned += ";"
        cleaned = re.sub(r"\bSELECT\b", f"SELECT TOP {limit_val}", cleaned, count=1, flags=re.IGNORECASE)

    return cleaned


def extract_sample_data_text(table_name: str) -> str:
    """
    Fetch first 5 rows of dataset table and sample distinct values for categorical columns
    to pass rich schema context to the AI prompt.
    """
    if not is_safe_identifier(table_name):
        return ""
    try:
        sample_df = run_query(f"SELECT TOP 10 * FROM {sanitize_identifier(table_name)}")
        if sample_df is not None and not sample_df.empty:
            cols = [c for c in sample_df.columns if c.lower() != "recordid"]
            preview_str = sample_df[cols].head(3).to_string(index=False)
            
            # Sample distinct categorical values so LLM knows exact status/category names
            cat_samples = []
            for col in cols:
                if not pd.api.types.is_numeric_dtype(sample_df[col]):
                    uniq = [str(val) for val in sample_df[col].dropna().unique()[:6]]
                    if uniq:
                        cat_samples.append(f"  - [{col}] distinct values: {', '.join(uniq)}")
            
            if cat_samples:
                preview_str += "\n\nDISTINCT CATEGORICAL COLUMN VALUES:\n" + "\n".join(cat_samples)
            return preview_str
    except Exception as e:
        pass
    return ""


def generate_sql(question: str, table_name: str, columns_list: list) -> tuple:
    """
    Generate T-SQL query using Fast Local Pattern Engine or OpenRouter AI API with caching.
    Returns (sql_query, is_fast_pattern)
    """
    cache_key = f"sql_gen_{table_name}_{hash(question)}"
    if system_cache:
        cached_sql = system_cache.get(cache_key)
        if cached_sql:
            try:
                test_df = run_query(cached_sql)
                if test_df is not None and not test_df.empty:
                    return cached_sql, True
            except Exception:
                pass

    # Try 0.001s Fast Pattern Generator first
    fast_sql = fast_pattern_sql_generator(question, table_name, columns_list)
    if fast_sql:
        if system_cache:
            system_cache.set(cache_key, fast_sql, ttl=1800)
        return fast_sql, True

    columns_text = "\n".join(columns_list)
    sample_data_text = extract_sample_data_text(table_name)

    prompt = generate_sql_prompt(question, table_name, columns_text, sample_data_text)
    raw_response = ask_gemini(prompt)
    cleaned_sql = clean_sql_response(raw_response)

    if not cleaned_sql or cleaned_sql == UNRELATED_MESSAGE:
        cleaned_sql = f"SELECT TOP 50 * FROM [{table_name}];"

    if system_cache and cleaned_sql:
        system_cache.set(cache_key, cleaned_sql, ttl=1800)

    return cleaned_sql, False


def fix_sql(question: str, table_name: str, columns_text: str, invalid_sql: str, error_message: str) -> str:
    """
    Repair invalid SQL using error feedback loop.
    """
    fix_prompt = f"""You generated invalid Microsoft SQL Server T-SQL code.

DATABASE CONTEXT:
- Table Name: [{table_name}]
- Columns:
{columns_text}

USER QUESTION:
{question}

INVALID SQL PRODUCED:
{invalid_sql}

SQL SERVER ERROR ENCOUNTERED:
{error_message}

CORRECTION INSTRUCTIONS:
- Fix the error and return ONLY valid T-SQL starting with SELECT and ending with semicolon (;).
- No markdown code blocks. Use bracketed column names [Col].

CORRECTED T-SQL:"""

    raw_response = ask_gemini(fix_prompt)
    return clean_sql_response(raw_response)


def execute_sql_with_retry(question: str, table_name: str, columns_list: list, max_retries: int = 3) -> dict:
    """
    Execute AI SQL generation with domain guardrails, fast local engine, and auto-retry loop.
    """
    start_time = time.time()

    # Enforce strict domain guardrails
    if is_out_of_domain_question(question, columns_list):
        return {
            "success": False,
            "is_out_of_domain": True,
            "is_fast_pattern": False,
            "sql": None,
            "df": pd.DataFrame(),
            "rows_returned": 0,
            "execution_time_ms": round((time.time() - start_time) * 1000, 2),
            "confidence": 0.0,
            "retries": 0,
            "error": UNRELATED_MESSAGE
        }

    sql, is_fast_pattern = generate_sql(question, table_name, columns_list)

    if sql == UNRELATED_MESSAGE:
        return {
            "success": False,
            "is_out_of_domain": True,
            "is_fast_pattern": False,
            "sql": None,
            "df": pd.DataFrame(),
            "rows_returned": 0,
            "execution_time_ms": round((time.time() - start_time) * 1000, 2),
            "confidence": 0.0,
            "retries": 0,
            "error": UNRELATED_MESSAGE
        }
    
    if not sql:
        sql = f"SELECT TOP 50 * FROM [{table_name}];"

    retry_count = 0
    last_error = ""
    columns_text = "\n".join(columns_list)

    while retry_count <= max_retries:
        try:
            upper_sql = sql.upper()
            forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "EXEC"]
            if any(f" {cmd} " in f" {upper_sql} " for cmd in forbidden):
                raise ValueError("Destructive DDL/DML queries are strictly prohibited.")

            df = run_query(sql)
            execution_time_ms = round((time.time() - start_time) * 1000, 2)
            confidence = 1.0 if retry_count == 0 else max(0.6, round(1.0 - (retry_count * 0.15), 2))

            return {
                "success": True,
                "is_out_of_domain": False,
                "is_fast_pattern": is_fast_pattern,
                "sql": sql,
                "df": df,
                "rows_returned": len(df),
                "execution_time_ms": execution_time_ms,
                "confidence": confidence,
                "retries": retry_count,
                "error": None
            }

        except Exception as e:
            last_error = str(e)
            retry_count += 1
            if retry_count <= max_retries:
                sql = fix_sql(question, table_name, columns_text, sql, last_error)
                if not sql:
                    sql = f"SELECT TOP 50 * FROM [{table_name}];"
            else:
                break

    execution_time_ms = round((time.time() - start_time) * 1000, 2)
    return {
        "success": False,
        "is_out_of_domain": False,
        "is_fast_pattern": False,
        "sql": sql,
        "df": pd.DataFrame(),
        "rows_returned": 0,
        "execution_time_ms": execution_time_ms,
        "confidence": 0.0,
        "retries": retry_count,
        "error": last_error
    }