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
    from ai.query_plan_engine import parse_question_to_query_plan, validate_concept_availability
    from ai.schema_intelligence import get_dataset_schema_intelligence
    from database.connection import run_query, is_safe_identifier, sanitize_identifier
    from utils.cache import system_cache
except ModuleNotFoundError:
    from sql_agent import generate_sql_prompt
    try:
        from semantic_matcher import resolve_query_semantic_columns
    except ModuleNotFoundError:
        resolve_query_semantic_columns = None
    parse_question_to_query_plan = None
    validate_concept_availability = None
    get_dataset_schema_intelligence = None
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


UNRELATED_MESSAGE = "I’m designed to analyze your uploaded dataset. Please ask a question related to the available data, such as customer records, companies, locations, dates, counts, trends, or other dataset fields."

# Priority list of ultra-fast working OpenRouter models (Sub-second to 2s response times)
FAST_MODELS = [
    "deepseek/deepseek-chat",
    "qwen/qwen-2.5-coder-32b-instruct",
    "meta-llama/llama-3.3-70b-instruct",
    "openai/gpt-4o-mini"
]



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
    Generate deep, readable, highly attractive ChatGPT/Claude style executive-level AI business insights.
    Directly answers missing values, duplicate records, first N records, and complex analytics queries.
    """
    q_low = question.lower().strip() if question else ""

    # 1. Special Handling: Missing Values Audit Question
    if any(kw in q_low for kw in ["missing value", "missing values", "null value", "null values", "empty cells", "missing data"]):
        if df is None or df.empty:
            return "### 🔍 Missing Values Audit\n\n**Direct Answer**: **No.** All columns have **0 missing values** (100% complete dataset)."
        
        null_counts = {}
        for col in df.columns:
            if col.endswith("_Nulls"):
                c_name = col.replace("_Nulls", "")
                null_counts[c_name] = int(df[col].iloc[0]) if not df[col].empty else 0
            elif not is_technical_or_id_column(col):
                null_counts[col] = int(df[col].isnull().sum())
        
        total_nulls = sum(null_counts.values())
        if total_nulls == 0:
            return (
                f"### 🔍 Missing Values Audit\n\n"
                f"**Direct Answer**: **No.** All **{len(null_counts)} columns** have **0 missing values** (100% complete dataset data integrity).\n\n"
                f"| Audit Status | Columns Audited | Total Missing Values | Data Completeness |\n"
                f"| :--- | :--- | :--- | :--- |\n"
                f"| ✅ **Passed** | **{len(null_counts)}** | **0** | **100.0%** |"
            )
        else:
            rows_str = "\n".join([f"- **{c}**: `{cnt:,}` missing values" for c, cnt in null_counts.items() if cnt > 0])
            return (
                f"### ⚠️ Missing Values Audit\n\n"
                f"**Direct Answer**: Found **{total_nulls:,} missing values** across columns:\n\n{rows_str}"
            )

    # 2. Special Handling: Percentage & Math Calculation Questions (Claude & ChatGPT Style)
    if "percentage" in q_low or "percent" in q_low or "%" in q_low or "Percentage" in df.columns:
        if df is not None and not df.empty:
            cols = df.columns.tolist()
            matching_cnt = None
            total_cnt = None
            pct_val = None
            
            for c in cols:
                if any(kw in c.lower() for kw in ["matching", "returned", "delivered", "count"]):
                    try: matching_cnt = int(df[c].iloc[0])
                    except Exception: pass
                elif "total" in c.lower():
                    try: total_cnt = int(df[c].iloc[0])
                    except Exception: pass
                elif "percentage" in c.lower() or "percent" in c.lower():
                    try: pct_val = float(df[c].iloc[0])
                    except Exception: pass

            if pct_val is not None:
                calc_str = f"{matching_cnt:,} / {total_cnt:,} × 100 = {pct_val:.2f}%" if (matching_cnt is not None and total_cnt is not None and total_cnt > 0) else f"{pct_val:.2f}%"
                
                return (
                    f"### 🎯 Percentage & Calculation Breakdown\n\n"
                    f"**Direct Answer**: **{pct_val:.2f}%**\n\n"
                    f"#### 📐 Step-by-Step Hand-to-Hand Calculation:\n"
                    f"```text\n{calc_str}\n```\n\n"
                    f"| Metric Name | Value |\n"
                    f"| :--- | :--- |\n"
                    f"| 🎯 **Target Filtered Count** | **{matching_cnt if matching_cnt is not None else 'N/A'}** |\n"
                    f"| 📊 **Total Dataset Records** | **{total_cnt if total_cnt is not None else 'N/A'}** |\n"
                    f"| 📈 **Calculated Percentage** | **{pct_val:.2f}%** |"
                )

    # 3. Special Handling: Total COD / Currency Sum Questions
    if any(kw in q_low for kw in ["total cod", "cod value", "total amount", "total value", "total revenue", "total price", "total cost", "total sum"]) or (df is not None and not df.empty and len(df) == 1 and any(kw in str(df.columns[0]).lower() for kw in ["total", "sum", "cod", "val"])):
        if df is not None and not df.empty and len(df) == 1:
            val = df.iloc[0, 0]
            try:
                numeric_val = float(val)
                val_formatted = f"Rs. {numeric_val:,.2f}" if numeric_val % 1 != 0 else f"Rs. {int(numeric_val):,}"
                raw_num_str = f"{int(numeric_val):,}" if numeric_val % 1 == 0 else f"{numeric_val:,.2f}"
            except Exception:
                val_formatted = f"{val}"
                raw_num_str = f"{val}"

            col_name = df.columns[0].replace("_", " ")
            return (
                f"### 💰 Total Financial Metric Summary\n\n"
                f"**Direct Answer**: **{val_formatted}** ({raw_num_str})\n\n"
                f"| Metric Name | Calculated Aggregate Amount |\n"
                f"| :--- | :--- |\n"
                f"| 💳 **{col_name}** | **{val_formatted}** |\n"
                f"| 📊 **Raw Amount Figure** | **{raw_num_str}** |"
            )

    # 4. Special Handling: Duplicate Customer IDs / Records Question
    if any(kw in q_low for kw in ["duplicate customer id", "duplicate customer ids", "duplicate ids", "duplicate records", "duplicates", "duplicate entries"]):
        if df is None or df.empty:
            return (
                "### 🔍 Duplicate Records Audit\n\n"
                "**Direct Answer**: **No duplicate Customer IDs.** All record identifiers in the dataset are **100% unique**.\n\n"
                "| Audit Status | Unique Records | Duplicate IDs | Integrity Status |\n"
                "| :--- | :--- | :--- | :--- |\n"
                "| ✅ **Passed** | **Unique** | **0** | **100.0% Clean** |"
            )
        else:
            return (
                f"### ⚠️ Duplicate Records Audit\n\n"
                f"**Direct Answer**: Found **{len(df):,} duplicate Customer IDs** in the dataset.\n\n"
                f"Top duplicates are displayed in the results table below."
            )

    # 5. Special Handling: First N Records Question
    if any(kw in q_low for kw in ["first 5", "first 10", "first 3", "first 20"]) or (re.search(r"\bfirst\s*\d+\b", q_low) and "show" in q_low):
        if df is not None and not df.empty:
            items = []
            for idx, row in df.head(5).iterrows():
                fname = row.get("First_Name", row.get("First Name", ""))
                lname = row.get("Last_Name", row.get("Last Name", ""))
                name_str = f"**{fname} {lname}**".strip() if (fname or lname) else f"Record #{idx+1}"
                city = row.get("City", "")
                country = row.get("Country", "")
                loc_str = f" — {city}" if city else ""
                loc_str += f" — {country}" if country else ""
                items.append(f"{idx+1}. {name_str}{loc_str}")
            
            return (
                f"### 📋 First {len(df)} Customer Records\n\n"
                f"**Direct Answer**: First records begin with:\n\n" + "\n".join(items)
            )

    # 4. Standard ChatGPT / Claude Style Enhanced Executive Summary
    if df is None or df.empty:
        return f"No matching records found in dataset `[{table_name}]` for query *'{question}'*."

    total_rows = len(df)
    total_cols = len(df.columns)

    real_numeric_cols = []
    for col in df.columns:
        if not is_technical_or_id_column(col) and pd.api.types.is_numeric_dtype(df[col]):
            valid_num = df[col].dropna()
            if not valid_num.empty and valid_num.max() < 10000000:
                real_numeric_cols.append(col)

    text_cols = [c for c in df.columns if not is_technical_or_id_column(c) and not pd.api.types.is_numeric_dtype(df[c])]

    summary_parts = []

    # Section 1: Executive Overview Card
    summary_parts.append(
        f"### 📊 Executive Query Summary\n\n"
        f"Retrieved **{total_rows:,} matching records** across **{total_cols} columns** from table `[{table_name}]`.\n\n"
        f"- 🎯 **Primary Question**: *\"{question}\"*\n"
        f"- ⚡ **Records Evaluated**: **{total_rows:,}**\n"
        f"- 📐 **Data Dimension**: **{total_rows:,} rows × {total_cols} columns**"
    )

    # Section 2: Key Breakdown & Categorical Distribution
    from utils.helpers import CITY_CODE_MAP
    cat_highlights = []
    for col in text_cols:
        col_low = col.lower()
        if any(k in col_low for k in ["country", "city", "status", "company", "state", "category", "type", "destination", "origin"]):
            val_counts = df[col].dropna().value_counts()
            if not val_counts.empty:
                top_name = str(val_counts.index[0])
                top_name_clean = f"{CITY_CODE_MAP.get(top_name.upper().strip(), top_name)} ({top_name})" if top_name.upper().strip() in CITY_CODE_MAP else top_name
                top_cnt = int(val_counts.iloc[0])
                pct = round((top_cnt / total_rows) * 100, 1)

                if len(val_counts) == 1:
                    cat_highlights.append(f"- **{col}**: All {total_rows:,} records belong to **{top_name_clean}** ({pct}%).")
                else:
                    items = []
                    for idx, val in val_counts.head(4).items():
                        idx_str = str(idx)
                        idx_clean = f"**{CITY_CODE_MAP.get(idx_str.upper().strip(), idx_str)} ({idx_str})**" if idx_str.upper().strip() in CITY_CODE_MAP else f"**{idx_str}**"
                        items.append(f"{idx_clean} (`{val:,}`)")
                    cat_highlights.append(f"- **{col}**: Highest distribution is {', '.join(items)}.")

    if cat_highlights:
        summary_parts.append("### 📌 Key Distribution Breakdown\n\n" + "\n".join(cat_highlights[:4]))

    # Section 3: Financial & Operational Metrics
    if real_numeric_cols:
        metric_items = []
        for col in real_numeric_cols[:4]:
            col_sum = df[col].sum()
            col_avg = df[col].mean()
            col_max = df[col].max()

            if pd.notna(col_sum) and abs(col_sum) > 0:
                metric_items.append(
                    f"- **{col}**: Total = **{col_sum:,.2f}** | Average = **{col_avg:,.2f}** | Peak = **{col_max:,.2f}**"
                )
        if metric_items:
            summary_parts.append("### 💰 Financial & Operational Metrics\n\n" + "\n".join(metric_items))

    # Section 4: Strategic Actionable Takeaway
    takeaway_text = f"The retrieved **{total_rows:,} records** demonstrate clear pattern distribution across dataset `{table_name}`. "
    if "return" in q_low:
        takeaway_text += "Management should investigate top destination areas with high return rates to reduce logistics overhead and improve fulfillment success."
    elif "customer" in q_low or "country" in q_low or "city" in q_low:
        takeaway_text += "Business teams should target top performing regions with tailored market strategies to accelerate customer acquisition and revenue growth."
    else:
        takeaway_text += "Business teams should leverage these filtered insights to streamline operations, optimize resource allocation, and enhance decision quality."

    summary_parts.append("### 💡 Strategic Recommendation\n\n" + takeaway_text)

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
        r"\bhow are you\b", r"\bwho are you\b", r"\bwhat is your name\b", r"\bwhat is my name\b", r"\bmy personal name\b", r"\btell me my personal name\b",
        r"\bwho am i\b", r"\bwhere am i\b", r"\bwho created you\b", r"\bwho developed you\b",
        r"\bwhat is my age\b", r"\bhow old am i\b", r"\bmy age\b", r"\bmy birthday\b", r"\bmy salary\b", r"\bmy identity\b",
        r"\bmy address\b", r"\bmy phone number\b", r"\bmy password\b", r"\bmy email\b", r"\bwhat is my\b", r"\bhow old\b",
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
    words = [w for w in cleaned.split() if len(w) >= 2 and not w.isdigit()]
    return " ".join(words).strip()


def fast_pattern_sql_generator(question: str, table_name: str, columns_list: list):
    """
    Sub-second (0.001s) Local T-SQL Rule Engine with intelligent pattern matching,
    column sorting, entity filtering, phone number searching, and exact record limit extraction.
    Returns (sql: str, params: tuple) with all user-derived literal values bound as
    parameters (never string-concatenated), or None if no local pattern matches.
    """
    question_norm = normalize_speech_phonetics(question)
    q_lower = question_norm.lower().strip()
    safe_tbl = sanitize_identifier(table_name)
    primary_pk_col = find_primary_key_column(columns_list)
    safe_pk = sanitize_identifier(primary_pk_col) if primary_pk_col else ""

    # Priority 0: Structured Query Plan Engine Resolution (0.001s Local Execution)
    if parse_question_to_query_plan:
        try:
            plan = parse_question_to_query_plan(question, table_name)
            if plan and (plan.get("limit") or plan.get("filters") or plan.get("computed_fields") or plan.get("order_by") or plan.get("intent")):
                if plan.get("intent") == "count" and not plan.get("filters"):
                    return f"SELECT COUNT(*) AS [Total_Records] FROM {safe_tbl};", ()

                select_items = []
                if plan.get("computed_fields"):
                    for cf in plan["computed_fields"]:
                        select_items.append(f"{cf['expression']} AS [{cf['name']}]")
                for sc in plan.get("select", []):
                    if sc not in [cf["name"] for cf in plan.get("computed_fields", [])]:
                        select_items.append(f"[{sc}]")

                select_clause = ", ".join(select_items) if select_items else "*"
                top_clause = f"TOP ({plan['limit']}) " if plan.get("limit") else ""
                plan_params = []
                if plan.get("filters"):
                    where_clause = f" WHERE {' AND '.join([f['sql_clause'] for f in plan['filters']])}"
                    for f in plan["filters"]:
                        plan_params.extend(f.get("params", []))
                else:
                    where_clause = ""
                group_clause = f" GROUP BY {', '.join([f'[{g}]' for g in plan['group_by']])}" if plan.get("group_by") else ""
                having_clause = " HAVING COUNT(*) > 1" if plan.get("intent") == "duplicate_records" else ""
                order_items = [f"{o['sql_safe_column']} {o['direction']}" for o in plan.get("order_by", [])]
                order_clause = f" ORDER BY {', '.join(order_items)}" if order_items else ""

                plan_sql = f"SELECT {top_clause}{select_clause} FROM {safe_tbl}{where_clause}{group_clause}{having_clause}{order_clause};"
                return plan_sql, tuple(plan_params)
        except Exception as plan_err:
            print("[QUERY PLAN NOTICE]:", plan_err)

    # 0. Check for "Show All / Full Data / Sara Data" intent
    is_show_all = any(kw in q_lower for kw in ["all", "sara", "sare", "sab", "poora", "pura", "tamam", "entire", "full", "everything"])

    # 0. Check for "Count" / "Total rows" intent without filters
    if re.search(r"\b(count|total|how many|kitni|kitne)\b.*\b(records|rows|data|entries|ross|raws|rose)\b", q_lower) and not any(kw in q_lower for kw in ["lahore", "karachi", "islamabad", "rawalpindi", "multan", "return", "delivered", "area", "status"]):
        return f"SELECT COUNT(*) AS [TotalRecords] FROM {safe_tbl};", ()

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
            return f"SELECT{limit_clause} * FROM {safe_tbl} ORDER BY {sanitize_identifier(sort_col)} {direction};", ()
        elif is_last_request:
            return (f"SELECT{limit_clause} * FROM {safe_tbl} ORDER BY {safe_pk} DESC;" if safe_pk else f"SELECT{limit_clause} * FROM {safe_tbl};"), ()
        elif is_top_request:
            return (f"SELECT{limit_clause} * FROM {safe_tbl} ORDER BY {safe_pk} ASC;" if safe_pk else f"SELECT{limit_clause} * FROM {safe_tbl};"), ()
        else:
            if is_show_all and not limit_val:
                return (f"SELECT * FROM {safe_tbl} ORDER BY {safe_pk} ASC;" if safe_pk else f"SELECT * FROM {safe_tbl};"), ()
            else:
                top_str = f"TOP {limit_val}" if limit_val else "TOP 100"
                return (f"SELECT {top_str} * FROM {safe_tbl} ORDER BY {safe_pk} ASC;" if safe_pk else f"SELECT {top_str} * FROM {safe_tbl};"), ()

    # Rule B: If target entity filter IS present (e.g. "Karachi", "Delivered", "ID 1005", "Ali")
    search_cols = list(columns_list)
    if search_cols:
        limit_clause = f"TOP {limit_val} " if limit_val else "TOP 500 "
        order_clause = f" ORDER BY {safe_pk} {'DESC' if is_last_request else 'ASC'}" if safe_pk else ""

        # Priority 0: Semantic Multi-Condition Resolution (e.g. "returned orders from Karachi")
        # NOTE: resolve_query_semantic_columns() only ever produces sql_clause fragments built
        # from sanitized identifiers and fixed whitelisted literals (status keywords, city name
        # dictionary) - it never interpolates raw user text, so filter_sql_where is safe to use
        # directly with no parameter binding required here.
        if resolve_query_semantic_columns:
            sem_res = resolve_query_semantic_columns(question, table_name, columns_list)
            if sem_res and sem_res.get("filter_count", 0) >= 2 and sem_res.get("filter_sql_where"):
                sem_sql = f"SELECT {limit_clause}* FROM {safe_tbl} WHERE {sem_res['filter_sql_where']}{order_clause};"
                try:
                    test_df = run_query(sem_sql)
                    if test_df is not None and not test_df.empty:
                        return sem_sql, ()
                except Exception:
                    pass

        # Priority 1: Check status/state specific columns for status queries (e.g. "delivered", "pending", "cancelled", "returned")
        status_cols = [c for c in search_cols if any(kw in c.lower() for kw in ["status", "state", "stage", "condition"])]
        if status_cols:
            status_exact_conditions = [f"CAST({sanitize_identifier(col)} AS NVARCHAR(MAX)) = ?" for col in status_cols]
            status_exact_params = tuple(target_entity for _ in status_cols)
            status_sql = f"SELECT {limit_clause}* FROM {safe_tbl} WHERE {' OR '.join(status_exact_conditions)}{order_clause};"
            try:
                test_df = run_query(status_sql, status_exact_params)
                if test_df is not None and not test_df.empty:
                    return status_sql, status_exact_params
            except Exception:
                pass

            status_like_conditions = [f"CAST({sanitize_identifier(col)} AS NVARCHAR(MAX)) LIKE ?" for col in status_cols]
            status_like_params = tuple(f"%{target_entity}%" for _ in status_cols)
            status_like_sql = f"SELECT {limit_clause}* FROM {safe_tbl} WHERE {' OR '.join(status_like_conditions)}{order_clause};"
            try:
                test_df = run_query(status_like_sql, status_like_params)
                if test_df is not None and not test_df.empty:
                    return status_like_sql, status_like_params
            except Exception:
                pass

        # Priority 2: Exact match across ALL columns
        exact_conditions = [f"CAST({sanitize_identifier(col)} AS NVARCHAR(MAX)) = ?" for col in search_cols]
        exact_params = tuple(target_entity for _ in search_cols)
        exact_sql = f"SELECT {limit_clause}* FROM {safe_tbl} WHERE {' OR '.join(exact_conditions)}{order_clause};"
        try:
            test_df = run_query(exact_sql, exact_params)
            if test_df is not None and not test_df.empty:
                return exact_sql, exact_params
        except Exception:
            pass

        # Priority 3: Phrase LIKE match across ALL columns
        phrase_conditions = [f"CAST({sanitize_identifier(col)} AS NVARCHAR(MAX)) LIKE ?" for col in search_cols]
        phrase_params = tuple(f"%{target_entity}%" for _ in search_cols)
        phrase_sql = f"SELECT {limit_clause}* FROM {safe_tbl} WHERE {' OR '.join(phrase_conditions)}{order_clause};"
        try:
            test_df = run_query(phrase_sql, phrase_params)
            if test_df is not None and not test_df.empty:
                return phrase_sql, phrase_params
        except Exception:
            pass

        # Priority 4: Key token match across ALL columns (Filter out stop words!)
        stop_words = {"what", "where", "when", "which", "who", "whom", "whose", "why", "how", "this", "that", "these", "those", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "my", "your", "his", "her", "its", "our", "their", "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "from", "up", "about", "into", "over", "after"}
        tokens = [t for t in target_entity.split() if len(t) >= 3 and t.lower() not in stop_words]
        if tokens:
            token_conditions = []
            token_params = []
            for token in tokens:
                for col in search_cols:
                    token_conditions.append(f"CAST({sanitize_identifier(col)} AS NVARCHAR(MAX)) LIKE ?")
                    token_params.append(f"%{token}%")
            token_sql = f"SELECT {limit_clause}* FROM {safe_tbl} WHERE {' OR '.join(token_conditions)}{order_clause};"
            try:
                test_df = run_query(token_sql, tuple(token_params))
                if test_df is not None and not test_df.empty:
                    return token_sql, tuple(token_params)
            except Exception:
                pass

        # If local heuristic filter yielded 0 rows or low confidence, return None to trigger LLM AI Engine
        return None

    return None


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
                timeout=5.0
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
        r"\bGRANT\b", r"\bREVOKE\b", r"\bCREATE\b", r"\bMERGE\b"
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
    except Exception:
        pass
    return ""


def generate_sql(question: str, table_name: str, columns_list: list) -> tuple:
    """
    Generate T-SQL query using Fast Local Pattern Engine or OpenRouter AI API with caching.
    Returns (sql_query, params, is_fast_pattern). params is always a tuple (possibly empty);
    any user-derived literal value in a fast-pattern query is bound as a parameter, never
    string-concatenated into the SQL text.
    """
    cache_key = f"sql_gen_{table_name}_{hash(question)}"
    if system_cache:
        cached = system_cache.get(cache_key)
        if cached:
            cached_sql, cached_params = cached
            try:
                test_df = run_query(cached_sql, cached_params or None)
                if test_df is not None and not test_df.empty:
                    return cached_sql, cached_params, True
            except Exception:
                pass

    # Try 0.001s Fast Pattern Generator first
    fast_result = fast_pattern_sql_generator(question, table_name, columns_list)
    if fast_result:
        fast_sql, fast_params = fast_result
        if system_cache:
            system_cache.set(cache_key, (fast_sql, fast_params), ttl=1800)
        return fast_sql, fast_params, True

    columns_text = "\n".join(columns_list)
    sample_data_text = extract_sample_data_text(table_name)

    prompt = generate_sql_prompt(question, table_name, columns_text, sample_data_text)
    raw_response = ask_gemini(prompt)
    cleaned_sql = clean_sql_response(raw_response)

    if not cleaned_sql or cleaned_sql == UNRELATED_MESSAGE:
        cleaned_sql = f"SELECT TOP 50 * FROM [{table_name}];"

    if system_cache and cleaned_sql:
        system_cache.set(cache_key, (cleaned_sql, ()), ttl=1800)

    return cleaned_sql, (), False


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

    sql, params, is_fast_pattern = generate_sql(question, table_name, columns_list)

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
        params = ()

    retry_count = 0
    last_error = ""
    columns_text = "\n".join(columns_list)

    while retry_count <= max_retries:
        try:
            upper_sql = sql.upper()
            forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "EXEC", "CREATE", "MERGE", "GRANT", "REVOKE", "EXECUTE"]
            if any(f" {cmd} " in f" {upper_sql} " for cmd in forbidden) or not upper_sql.lstrip().startswith("SELECT"):
                raise ValueError("Only read-only SELECT queries are permitted.")

            df = run_query(sql, params or None)
            execution_time_ms = round((time.time() - start_time) * 1000, 2)
            confidence = 1.0 if retry_count == 0 else max(0.6, round(1.0 - (retry_count * 0.15), 2))

            from visualization.chart_generator import generate_interactive_chart_spec
            from visualization.chart_selector import select_chart
            chart_type = select_chart(df) or "pie"
            chart_spec = generate_interactive_chart_spec(df, chart_type) if df is not None and not df.empty else {}
            # NOTE: the Matplotlib PNG chart is intentionally NOT generated here anymore.
            # It's only needed for the "Download HD Chart" button and document exports, both
            # of which regenerate it on demand from session["last_sql"] - see
            # frontend.download_chart() - so a query the user never downloads no longer pays
            # for a Matplotlib render it doesn't need. The Plotly chart_spec above is what
            # actually renders on the page.

            return {
                "success": True,
                "is_out_of_domain": False,
                "is_fast_pattern": is_fast_pattern,
                "sql": sql,
                "df": df,
                "chart": None,
                "chart_spec": chart_spec,
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
                # fix_sql() always produces a fresh, fully self-contained SQL string
                # (no bound placeholders), so params must be reset alongside it.
                sql = fix_sql(question, table_name, columns_text, sql, last_error)
                params = ()
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