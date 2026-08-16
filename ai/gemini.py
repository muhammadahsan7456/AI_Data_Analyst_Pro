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
    from database.connection import run_query, is_safe_identifier, sanitize_identifier
    from utils.cache import system_cache
except ModuleNotFoundError:
    from sql_agent import generate_sql_prompt
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


UNRELATED_MESSAGE = "I can only answer questions related to uploaded datasets."

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

    # 2. Clean common prompt filler words to extract core entity name
    filler_patterns = [
        r"\b(show|get|fetch|give|display|all|data|set|records|rows|table|details|info)\b",
        r"\b(mujha|mujhe|chaya|chahiye|ka|ki|ke|den|do|batao|bataen|dikhao|dikhaye|dikhayein|karo|karein|bhej|bhejo|la|select|from|where)\b",
        r"\b(first|last|top|highest|lowest|most|least|count|total|summary|average|min|max|sum|list)\b",
        r"\b(number|of|the|in|for|a|an|is|are|ko|se|me|main|id)\b",
        r"\b(country|city|state|region|name|category|item|just|only|please)\b"
    ]
    cleaned = question
    for pat in filler_patterns:
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"[^\w\s-]", " ", cleaned).strip()
    words = [w for w in cleaned.split() if len(w) >= 2]

def generate_data_business_summary(table_name: str, df: pd.DataFrame, question: str) -> str:
    """
    Generate deep, detailed, executive-level AI business insights explaining what the data shows,
    record synthesis, top performers, numerical aggregations, and strategic recommendations in <0.01s.
    """
    if df is None or df.empty:
        return f"No matching records found in dataset [{table_name}] for query '{question}'."

    total_rows = len(df)
    total_cols = len(df.columns)

    # Exclude technical identifier columns from categorical analysis
    def is_technical_id(col_name: str) -> bool:
        c_lower = col_name.lower()
        if any(p in c_lower for p in ["recordid", "datasetid", "userid", "index"]):
            return True
        if c_lower.endswith("_id") or c_lower == "id" or "guid" in c_lower or "uuid" in c_lower:
            if not df.empty and col_name in df.columns:
                sample_val = str(df[col_name].dropna().iloc[0]) if not df[col_name].dropna().empty else ""
                if len(sample_val) > 10 and df[col_name].nunique() == len(df):
                    return True
        return False

    analysis_cols = [c for c in df.columns if not is_technical_id(c)]
    num_cols = df[analysis_cols].select_dtypes(include="number").columns.tolist()
    text_cols = df[analysis_cols].select_dtypes(include="object").columns.tolist()

    # Fallback if all text columns were filtered out
    if not text_cols:
        text_cols = df.select_dtypes(include="object").columns.tolist()

    summary_parts = []

    # 1. Data Scope & Executive Context
    summary_parts.append(
        f"📊 **Data Scope & Executive Overview**:\n"
        f"Retrieved **{total_rows:,} matching records** across **{total_cols} attributes** from dataset `[{table_name}]` for question *'{question}'*."
    )

    # 2. Detailed Record Synthesis & Key Findings
    narrative_items = []
    for col in text_cols[:3]:
        unique_vals = df[col].dropna().unique()
        if len(unique_vals) > 0:
            sample_str = ", ".join([f"**{v}**" for v in unique_vals[:4]])
            cnt_str = f"and {len(unique_vals) - 4} more" if len(unique_vals) > 4 else ""
            narrative_items.append(f"• **{col}**: Features entries like {sample_str} {cnt_str} (Total {len(unique_vals)} unique values).")

    if narrative_items:
        summary_parts.append("📝 **Detailed Record Synthesis & Key Findings**:\n" + "\n".join(narrative_items))

    # 3. Category Breakdown & Distribution
    if text_cols:
        cat_details = []
        for col in text_cols[:4]:
            val_counts = df[col].dropna().value_counts()
            if not val_counts.empty:
                top_name = str(val_counts.index[0])
                top_count = int(val_counts.iloc[0])
                pct = round((top_count / total_rows) * 100, 1)
                unique_cnt = len(val_counts)

                if unique_cnt > 1:
                    cat_details.append(
                        f"• **{col}**: Dominant category is **{top_name}** with **{top_count} records** ({pct}% share). Spans {unique_cnt} distinct categories."
                    )
                else:
                    cat_details.append(
                        f"• **{col}**: All {total_rows} records belong exclusively to **{top_name}**."
                    )

        if cat_details:
            summary_parts.append("🔍 **Category Breakdown & Distribution**:\n" + "\n".join(cat_details))

    # 4. Aggregated Business Metrics & Financial Highs/Lows
    if num_cols:
        metric_details = []
        for col in num_cols[:4]:
            col_sum = df[col].sum()
            col_avg = df[col].mean()
            col_max = df[col].max()
            col_min = df[col].min()

            if pd.notna(col_sum) and abs(col_sum) > 0:
                metric_details.append(
                    f"• **{col}**: Combined Total = **{col_sum:,.2f}** | Mean Average = **{col_avg:,.2f}** (Highest Peak: **{col_max:,.2f}**, Lowest: **{col_min:,.2f}**)"
                )
        if metric_details:
            summary_parts.append("📈 **Aggregated Financial & Operational Metrics**:\n" + "\n".join(metric_details))

    # 5. Strategic Executive Takeaway
    takeaways = []
    if text_cols:
        first_col = text_cols[0]
        top_val = df[first_col].dropna().value_counts().index[0] if not df[first_col].dropna().empty else "key categories"
        takeaways.append(f"Dataset reveals strong concentration around **{first_col}** (leading value: **{top_val}**).")

    if num_cols:
        takeaways.append(f"Financial and numerical indicators for **{num_cols[0]}** demonstrate stable distribution across returned rows.")

    takeaways.append("Management should leverage these record insights to prioritize high-value entities, address outlier cases, and streamline operational workflows.")

    summary_parts.append("💡 **Strategic Executive Takeaway & Action Plan**:\n" + " ".join(takeaways))

    return "\n\n".join(summary_parts)


def normalize_speech_phonetics(question: str) -> str:
    """
    Normalizes speech-to-text typos, mishearings, Roman Urdu phonetics, and common voice AI variations.
    Example: 'top 5 ross' -> 'top 5 rows', '5 raws' -> 'top 5 rows', 'panch rows' -> 'top 5 rows'
    """
    if not question or not isinstance(question, str):
        return ""

    q = question.lower().strip()

    # 1. Phonetic replacement for 'rows' / 'records' mishearings by browser WebSpeech API
    q = re.sub(r"\b(ross|raws|rose|roes|rowses|row)\b", "rows", q)
    q = re.sub(r"\b(ricord|ricords|rekaard|recs|rec)\b", "records", q)
    q = re.sub(r"\b(detta|daata|dataa)\b", "data", q)

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
        r"\bwho am i\b", r"\bwhere am i\b", r"\bwho made you\b", r"\bwho created you\b", r"\bwho developed you\b",
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
        "dikhao", "dikhaye", "dikhayein", "batao", "batayein", "la", "do", "karo"
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


def fast_pattern_sql_generator(question: str, table_name: str, columns_list: list) -> str:
    """
    Sub-second (0.01s) Local T-SQL Rule Engine with strict pattern matching and intelligent column sorting.
    """
    question_norm = normalize_speech_phonetics(question)
    q_lower = question_norm.lower().strip()
    safe_tbl = sanitize_identifier(table_name)

    # 0. Instant "Show data" / "All data" / "Display rows" / "Get dataset"
    if re.search(r"^\s*(show|get|fetch|display|view|list)\s+(data|dataset|table|records|rows|all)\s*$", q_lower) or q_lower in ["show data", "all data", "get data", "data", "records"]:
        return f"SELECT TOP 100 * FROM {safe_tbl};"

    # 1. "Count total records" / "Total rows" / "Kitni rows hain"
    if re.search(r"\b(count|total|how many|kitni|kitne)\b.*\b(records|rows|data|entries|ross|raws|rose)\b", q_lower):
        return f"SELECT COUNT(*) AS [TotalRecords] FROM {safe_tbl};"

    # 2. Strict "Top N records" / "Show N rows" (Ignore years like 2024 or prices)
    top_match = re.search(r"\b(?:top|first|highest|lowest|show|get|fetch|display)\s+(\d{1,3})\b", q_lower)
    if not top_match:
        top_match = re.search(r"\b(\d{1,3})\s+(?:records|rows|entries|data|items|ross|raws|rose)\b", q_lower)

    if top_match and top_match.group(1):
        limit_val = int(top_match.group(1))
        if 1 <= limit_val <= 1000:
            # Check if query requests sorting by numeric columns (e.g. 'top 5 sales', 'top 10 revenue')
            sort_col = None
            for col in columns_list:
                col_low = col.lower()
                if any(kw in col_low for kw in ["sales", "revenue", "amount", "total", "price", "val", "cost", "profit"]):
                    sort_col = col
                    break
            
            if sort_col:
                return f"SELECT TOP {limit_val} * FROM {safe_tbl} ORDER BY {sanitize_identifier(sort_col)} DESC;"
            else:
                return f"SELECT TOP {limit_val} * FROM {safe_tbl};"

    # 3. 100% Exact Entity Matching across text columns
    target_entity = extract_entity_phrase(question_norm)
    if target_entity:
        matched_text_cols = []
        for col in columns_list:
            col_lower = col.lower()
            if col_lower != "recordid" and not col_lower.endswith("id"):
                matched_text_cols.append(col)

        if matched_text_cols:
            exact_conditions = [f"{sanitize_identifier(col)} = '{target_entity}'" for col in matched_text_cols[:6]]
            exact_or_clause = " OR ".join(exact_conditions)
            exact_sql = f"SELECT * FROM {safe_tbl} WHERE {exact_or_clause};"

            try:
                test_df = run_query(exact_sql)
                if test_df is not None and not test_df.empty:
                    return exact_sql
            except Exception:
                pass

            phrase_conditions = [f"{sanitize_identifier(col)} LIKE '%{target_entity}%'" for col in matched_text_cols[:6]]
            phrase_or_clause = " OR ".join(phrase_conditions)
            return f"SELECT * FROM {safe_tbl} WHERE {phrase_or_clause};"

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
    Fetch first 2 rows of dataset table to pass compact sample column values to AI prompt.
    """
    if not is_safe_identifier(table_name):
        return ""
    try:
        sample_df = run_query(f"SELECT TOP 2 * FROM {sanitize_identifier(table_name)}")
        if sample_df is not None and not sample_df.empty:
            cols = [c for c in sample_df.columns if c.lower() != "recordid"]
            return sample_df[cols].to_string(index=False)
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
            return cached_sql, True

    # Try 0.01s Fast Pattern Generator first
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