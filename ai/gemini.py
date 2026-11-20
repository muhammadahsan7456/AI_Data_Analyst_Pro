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
            base_url="https://openrouter.ai/api/v1",
            max_retries=0,
            timeout=2.5
        )
    except Exception:
        client = None


UNRELATED_MESSAGE = "I'm designed to analyze your uploaded dataset only. Please ask a question about your data."

# Shown when no layer could turn a data question into a query. Previously the code fell
# back to "SELECT TOP 50 *" and presented those rows as if they answered the question.
NOT_UNDERSTOOD_MESSAGE = "I couldn't match that question to the columns in this dataset. Try naming a column or value, for example \"total {metric} by {group}\"."

# Sentinel prefix used the same way as UNRELATED_MESSAGE: fast_pattern_sql_generator()
# returns it in place of real SQL when the query plan flagged something in the question
# that isn't actually in this dataset (a value like "Sialkot" not present in any column,
# or a metric like "shipping_cost" that matches no real column). Without this, the plan's
# best-effort SQL still runs - typically an unfiltered COUNT(*) or a SUM of the wrong
# column - and the user gets a confident, wrong-looking-plausible number instead of being
# told the data isn't there. execute_sql_with_retry() checks for this prefix before
# treating the string as SQL.
DATA_NOT_FOUND_PREFIX = "__ASKAI_DATA_NOT_FOUND__:"

# Priority list of ultra-fast working OpenRouter models (Sub-second to 2s response times).
# Every entry here has been confirmed with a real OpenRouter API call to actually respond
# (not just assumed from the model name) - see the "AI model" dropdown on the Enterprise
# Settings page, whose option values are kept in sync with this exact list.
FAST_MODELS = [
    "openai/gpt-4o-mini",
    "meta-llama/llama-3.3-70b-instruct",
    "qwen/qwen-2.5-coder-32b-instruct",
    "openai/gpt-3.5-turbo",
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


def generate_data_business_summary(table_name: str, df: pd.DataFrame, question: str, preferred_model: str = None, privacy_level: int = 3, ai_timeout_seconds: float = 2.0) -> tuple:
    """
    Generate deep, readable, highly attractive ChatGPT/Claude style executive-level AI business insights.
    Directly answers user's specific analytical query across ANY dataset domain (Sales, HR, Logistics, Finance, etc.).
    Uses OpenRouter LLM with ultra-fast fallback to dynamic statistical analysis.

    Returns (text, model_used). model_used is None whenever the answer came from one of the
    deterministic fast-path/fallback branches below rather than the LLM - callers that only
    care about the text can do `text, _ = generate_data_business_summary(...)`.

    privacy_level (Enterprise Settings "Privacy Level", previously saved to the session
    but never actually read anywhere) controls how much of the real row data is included
    in the prompt sent to the third-party LLM API - see ai.privacy_guard.
    sanitize_prompt_payload for the 4 levels. The computed stats_hints below (sum/avg/
    min/max) are always included regardless of level: they're server-side arithmetic, not
    raw PII-bearing rows, and at the default level (3) row-level masking only touches
    columns actually matching a PII pattern (email/phone/SSN/etc.) - a normal business
    question like "sales by city" is unaffected either way, since City/Sales aren't PII.
    """
    if df is None or df.empty:
        return f"No matching records found in dataset `[{table_name}]` for query: *\"{question}\"*.\n\nPlease check spelling or try broadening your search filter.", None

    q_low = question.lower().strip() if question else ""

    # 1. Fast Special Handling: Missing Values Audit Question (if specifically asked)
    if any(kw in q_low for kw in ["missing value", "missing values", "null value", "null values", "empty cells", "missing data"]):
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
                f"### Missing Values Audit\n\n"
                f"**Direct Answer**: **No missing values detected.** All **{len(null_counts)} columns** have **0 missing values** (100% data integrity).\n\n"
                f"| Audit Status | Columns Audited | Total Missing Values | Data Completeness |\n"
                f"| :--- | :--- | :--- | :--- |\n"
                f"| **Passed** | **{len(null_counts)}** | **0** | **100.0%** |"
            ), None
        else:
            rows_str = "\n".join([f"- **{c}**: `{cnt:,}` missing values" for c, cnt in null_counts.items() if cnt > 0])
            return (
                f"### Missing Values Audit\n\n"
                f"**Direct Answer**: Found **{total_nulls:,} missing values** across audited columns:\n\n{rows_str}"
            ), None

    # 2. Fast Special Handling: Duplicate Records Audit (if specifically asked)
    if any(kw in q_low for kw in ["duplicate customer id", "duplicate customer ids", "duplicate ids", "duplicate records", "duplicates", "duplicate entries"]):
        if df.empty or (len(df) == 1 and str(df.iloc[0, 0]) in ("0", "None", "")):
            return (
                "### Duplicate Records Audit\n\n"
                "**Direct Answer**: **No duplicate records found.** All record identifiers in the dataset are **100% unique**.\n\n"
                "| Audit Status | Unique Records | Duplicate IDs | Integrity Status |\n"
                "| :--- | :--- | :--- | :--- |\n"
                "| **Passed** | **Unique** | **0** | **100.0% Clean** |"
            ), None
        else:
            return (
                f"### Duplicate Records Audit\n\n"
                f"**Direct Answer**: Found **{len(df):,} duplicate records** in the dataset.\n\n"
                f"Top duplicates are displayed in the results table below."
            ), None

    # 3. Comprehensive Executive Summary via LLM (ChatGPT / Claude / Gemini quality)
    if client and api_key:
        try:
            total_rows = len(df)
            cols_to_show = [c for c in df.columns if c.lower() != "recordid"]

            if privacy_level in (1, 2):
                # Levels 1-2 are an intentional detail-for-privacy tradeoff the user
                # explicitly chose (schema-only / aggregates-only, no raw rows at all) -
                # sanitize_prompt_payload's own text already includes its own framing.
                from ai.privacy_guard import sanitize_prompt_payload
                df_preview = sanitize_prompt_payload(question, df[cols_to_show], privacy_level)
            elif total_rows <= 25:
                df_preview = df[cols_to_show].to_string(index=False)
            else:
                head_str = df[cols_to_show].head(10).to_string(index=False)
                tail_str = df[cols_to_show].tail(3).to_string(index=False)
                df_preview = f"{head_str}\n... [{total_rows - 13} additional rows omitted] ...\n{tail_str}"

            if privacy_level == 3:
                # Default level: PII-pattern columns/values masked, everything else
                # (category/metric columns a normal business question needs) untouched.
                from ai.privacy_guard import mask_dataframe_pii
                df_preview_source = mask_dataframe_pii(df[cols_to_show])
                if total_rows <= 25:
                    df_preview = df_preview_source.to_string(index=False)
                else:
                    head_str = df_preview_source.head(10).to_string(index=False)
                    tail_str = df_preview_source.tail(3).to_string(index=False)
                    df_preview = f"{head_str}\n... [{total_rows - 13} additional rows omitted] ...\n{tail_str}"

            stats_hints = []
            numeric_cols = [c for c in cols_to_show if pd.api.types.is_numeric_dtype(df[c]) and not is_technical_or_id_column(c)]
            for nc in numeric_cols[:4]:
                s = df[nc].dropna()
                if not s.empty:
                    stats_hints.append(f"Column [{nc}]: Sum={s.sum():,.2f}, Avg={s.mean():,.2f}, Min={s.min():,.2f}, Max={s.max():,.2f}")
            
            stats_context = ("\nCOMPUTED METRIC STATS:\n" + "\n".join(stats_hints)) if stats_hints else ""

            llm_summary_prompt = f"""You are an elite Chief Business Data Analyst and Intelligence Advisor.
A stakeholder asked this specific question about their uploaded dataset table [{table_name}]:
"{question}"

DATASET CONTEXT:
- Table: [{table_name}]
- Total Records Returned: {total_rows:,}
- Columns in Result: {', '.join(cols_to_show)}
{stats_context}

QUERY RESULT DATA:
{df_preview}

TASK:
Write a comprehensive, professional, executive-ready analytical response in clean Markdown directly answering what was asked.
Adapt your analysis to whatever domain this dataset represents (Sales, Finance, HR, Employees, Operations, Logistics, Healthcare, E-Commerce, etc.).

REQUIRED STRUCTURE:
### Direct Answer
State the exact, direct answer to the user's question upfront in 1-2 bold, definitive sentences. Highlight the exact metric, number, ranking, or entity asked for.

### In-Depth Analytical Findings & Key Metrics
Provide 3 to 5 structured bullet points detailing:
- Exact numbers, totals, averages, percentages, and proportions with **bold values**.
- Top performers, categories, segments, or distributions visible in the data.
- Meaningful comparisons, ratios, or trends.

### Strategic Business Takeaway & Recommendations
Provide 1-2 concrete, domain-specific actionable business recommendations based directly on these numbers.

STRICT GUIDELINES:
- Ground everything 100% in the provided result data. Never hallucinate or use generic placeholders.
- Do NOT mention SQL, queries, databases, Python, or tech stack.
- Return ONLY clean Markdown text."""

            # Enterprise Settings "Max AI Response Timeout" - previously saved to the
            # session but never read anywhere. This is a per-model-attempt budget (the
            # multi-model fallback loop in ask_gemini can still try up to 4 models), not a
            # total request ceiling, so the UI's "Fast Multi-model Fallback" framing holds
            # at any of the 3 offered values.
            ai_summary, model_used = ask_gemini(llm_summary_prompt, max_tokens=450, timeout=ai_timeout_seconds, preferred_model=preferred_model)
            if ai_summary and len(ai_summary.strip()) > 50 and ("Direct Answer" in ai_summary or "Answer" in ai_summary):
                print(f"[AI MODEL USED] explanation generated by: {model_used}")
                return ai_summary.strip(), model_used
        except Exception:
            pass

    # 4. High-Quality Dynamic Fallback if LLM unavailable or timed out
    # Dynamically analyzes ANY dataset without hardcoded domain assumptions
    total_rows = len(df)
    total_cols = len(df.columns)
    
    # 4a. If single-row, single-column scalar result
    if total_rows == 1 and total_cols == 1:
        col_name = str(df.columns[0]).replace("_", " ")
        val = df.iloc[0, 0]
        try:
            val_num = float(val)
            val_fmt = f"{int(val_num):,}" if val_num.is_integer() else f"{val_num:,.2f}"
        except (ValueError, TypeError):
            val_fmt = str(val)
        return (
            f"### Direct Answer\n\n"
            f"The **{col_name}** is **{val_fmt}**.\n\n"
            f"| Metric Name | Value |\n"
            f"| :--- | :--- |\n"
            f"| **{col_name}** | **{val_fmt}** |\n\n"
            f"### Key Takeaway\n\n"
            f"This metric was aggregated directly across the matching records in dataset `[{table_name}]`."
        ), None

    # 4b. Multi-row or multi-column dynamic statistical summary
    summary_parts = []
    
    # Direct answer sentence
    summary_parts.append(
        f"### Direct Answer\n\n"
        f"Retrieved **{total_rows:,} matching records** across **{total_cols} attributes** from dataset `[{table_name}]` answering *\"{question}\"*."
    )

    # Key breakdowns
    findings = []
    text_cols = [c for c in df.columns if not is_technical_or_id_column(c) and not pd.api.types.is_numeric_dtype(df[c])]
    numeric_cols = [c for c in df.columns if not is_technical_or_id_column(c) and pd.api.types.is_numeric_dtype(df[c])]

    for tc in text_cols[:3]:
        vc = df[tc].dropna().value_counts()
        if not vc.empty:
            top_val = vc.index[0]
            top_cnt = vc.iloc[0]
            pct = round((top_cnt / total_rows) * 100, 1)
            if len(vc) == 1:
                findings.append(f"- **{tc}**: All **{total_rows:,} records** belong to **{top_val}** ({pct}%).")
            else:
                top_items = [f"**{idx}** (`{cnt:,}`)" for idx, cnt in vc.head(3).items()]
                findings.append(f"- **{tc} Breakdown**: Leading categories are {', '.join(top_items)}.")

    for nc in numeric_cols[:3]:
        s = df[nc].dropna()
        if not s.empty:
            col_sum = s.sum()
            col_avg = s.mean()
            col_max = s.max()
            findings.append(f"- **{nc} Metrics**: Total = **{col_sum:,.2f}** | Average = **{col_avg:,.2f}** | Peak = **{col_max:,.2f}**")

    if findings:
        summary_parts.append("### In-Depth Analytical Findings & Key Metrics\n\n" + "\n".join(findings))

    summary_parts.append(
        f"### Strategic Business Takeaway & Recommendations\n\n"
        f"- **Data-Driven Optimization**: Prioritize highest-performing segments identified in this query to maximize operational efficiency and return on investment.\n"
        f"- **Continuous Monitoring**: Track key indicators in dataset `[{table_name}]` regularly to detect shifting patterns early."
    )

    return "\n\n".join(summary_parts), None


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


def is_out_of_domain_question(question: str, columns_list: list, table_name_hint: str = None) -> bool:
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
    # "who is the top customer", "what is the highest profit": these phrasings start like
    # general-knowledge questions but ask about the data. Allow them when they name a column
    # of this dataset or a ranking/aggregation word.
    _names_col_early = any(re.search(r"\b" + re.escape(str(c).lower()) + r"s?\b", q_lower) for c in (columns_list or []) if len(str(c)) >= 3)
    _data_rank_words = re.search(r"\b(top|highest|lowest|best|worst|most|least|total|sum|count|average|avg|maximum|minimum|records?|rows?)\b", q_lower)
    for pattern in general_patterns:
        if re.search(pattern, q_lower):
            if _names_col_early and _data_rank_words:
                continue
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

    # 3b. Other clearly off-topic subjects (weather, prices of things outside the file,
    # entertainment, advice). Roman Urdu included. A question that ALSO names a column of
    # this dataset is still allowed through (e.g. a dataset with a "Weather" column).
    off_topic_patterns = [
        r"\bweather\b", r"\bmausam\b", r"\btemperature (?:in|of|today|tomorrow)\b", r"\bforecast for\b",
        r"\bbitcoin\b", r"\bcrypto(?:currency)?\b", r"\bstock price\b", r"\bgold (?:rate|price)\b", r"\bdollar rate\b",
        r"\bmovie\b", r"\bfilm\b", r"\bsong\b", r"\bnetflix\b", r"\bcricket score\b", r"\bmatch score\b",
        r"\bnews\b", r"\brecipe\b", r"\btranslate\b", r"\brecommend (?:a|me|some)\b", r"\bsuggest (?:a|me) (?:movie|book|song|place)\b",
        r"\bexplain (?:quantum|physics|chemistry|biology|relativity|history|philosophy)\b", r"\bquantum\b",
        r"\bwho invented\b", r"\bwhen did\b.*\b(?:war|independence|die|born)\b", r"\bhow (?:do|can) i (?:cook|lose weight|learn)\b",
        r"\bjavascript\b", r"\bpython\b", r"\bhtml\b", r"\bcss\b", r"\bjava\b", r"\bc\+\+", r"\bprogramming\b",
        r"\bsyntax error\b", r"\bstack ?overflow\b", r"\bundefined (?:error|variable|is not)\b", r"\bcompil(?:e|er|ing)\b",
        r"\bhow (?:do|can) i fix\b.*\b(?:error|bug|code|exception|crash)\b", r"\bfix (?:my|the|this|a|an) (?:code|bug|script|program)\b",
        r"\bdebug\b", r"\bsql injection\b", r"\bchatgpt\b", r"\bopenai\b", r"\bjoke\b", r"\bpoem\b", r"\bstory\b",
    ]
    col_words = set()
    for col in columns_list:
        for part in re.split(r"[^a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", str(col)).lower()):
            if len(part) >= 3:
                col_words.add(part)
    words = set(re.findall(r"\b[a-z0-9_-]+\b", q_lower))
    names_a_column = bool(words & col_words) or any(str(c).lower() in q_lower for c in columns_list if len(str(c)) >= 3)
    names_full_column = any(re.search(r"\b" + re.escape(str(c).lower()) + r"\b", q_lower) for c in columns_list if len(str(c)) >= 3)
    def _dataset_values():
        try:
            from ai.value_matcher import get_column_vocabulary
            vocab = get_column_vocabulary(table_name_hint) if table_name_hint else {}
            return [str(v).lower() for vals in (vocab or {}).values() for v in vals]
        except Exception:
            return []

    def _names_dataset_value():
        # A proper noun that is actually a value in this dataset (a city, a product).
        return any(len(v) >= 3 and re.search(r"\b" + re.escape(v) + r"\b", q_lower) for v in _dataset_values())

    if not names_full_column:
        for pattern in off_topic_patterns:
            hit = re.search(pattern, q_lower)
            if hit:
                # The off-topic word is itself part of a value in this data (a product
                # called "Python Course", a "Rainy weather" category): a data question.
                word = hit.group(0).strip()
                if word and any(re.search(r"\b" + re.escape(word) + r"\b", v) for v in _dataset_values()):
                    break
                return True

    # 4. Dataset relevance: the question must reference this dataset (a column, a value
    # that exists in it) or carry clear analytical intent. Short grammatical particles
    # ("me", "se", "ka", "do") were previously counted as data keywords, which let almost
    # any Roman Urdu sentence through; they are no longer evidence on their own.
    if names_a_column:
        return False

    analytic_keywords = {
        "show", "list", "count", "total", "sum", "average", "avg", "mean", "median", "max", "maximum",
        "min", "minimum", "highest", "lowest", "top", "bottom", "first", "last", "records", "rows", "row",
        "ross", "raws", "recs", "data", "dataset", "table", "summary", "chart", "graph", "plot", "trend",
        "distribution", "breakdown", "column", "columns", "values", "filter", "group", "sort", "compare",
        "comparison", "percentage", "percent", "share", "growth", "monthly", "yearly", "weekly", "daily",
        "orders", "order", "entries", "duplicates", "missing", "null", "nulls", "unique", "distinct",
        "kitne", "kitni", "kitna", "zyada", "ziada", "kam", "sab", "dikhao", "dikhaye", "batao", "batayein",
        "jama", "ausat", "mahina", "saal", "fetch", "display", "give", "find", "which", "how", "many", "much",
    }
    if words & analytic_keywords:
        return False

    # A proper noun that is actually a value in this dataset (e.g. a city or product name).
    if _names_dataset_value():
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


_PURE_COUNT_PATTERNS = [
    r"^\s*(?:what\s+is\s+the\s+)?(?:total\s+)?(?:count|number)\s+(?:of\s+)?(?:records|rows|data|entries|all\s+records)\s*\??\s*$",
    r"^\s*(?:total\s+)?(?:records|rows|data|entries)\s+(?:count|kitne\s+hain|kitni\s+hain)\s*\??\s*$",
    r"^\s*(?:how\s+many|kitne|kitni)\s+(?:total\s+)?(?:records|rows|data|entries)\s*(?:are\s+there|are\s+in\s+(?:this|the)\s+(?:dataset|file|table)|hain|hai)?\s*\??\s*$",
]
_GENERIC_QUALIFIERS = {
    "total", "average", "avg", "sum", "max", "maximum", "min", "minimum", "highest", "lowest", "the", "of",
    "per", "unit", "net", "gross", "overall", "mean", "median", "my", "our", "all", "each", "and", "by", "in",
}
_MEASURE_WORDS = r"(?:cost|costs|fee|fees|charges?|tax|taxes|discount|commission|amount|revenue|salary|salaries|income|expenses?|margin|rating|score|weight)"
_TIME_GRAIN = [
    (r"\b(?:monthly|by\s+month|per\s+month|each\s+month|month\s*wise|month\s+by\s+month|mahana|har\s+mahine)\b", "month"),
    (r"\b(?:yearly|annual|by\s+year|per\s+year|each\s+year|year\s*wise|saalana|har\s+saal)\b", "year"),
]


def _numeric_and_date_columns(table_name: str, columns_list: list):
    try:
        from ai.schema_intelligence import get_dataset_schema_intelligence
        from ai.metric_resolver import get_real_numeric_columns
        si = get_dataset_schema_intelligence(table_name)
        nums = list(get_real_numeric_columns(si) or [])
        dates = list(si.get("date_columns") or [])
        if isinstance(si.get("date_columns"), dict):
            dates = list(si["date_columns"].keys())
        return nums, dates
    except Exception:
        return [], []


def _early_patterns(q_lower: str, table_name: str, safe_tbl: str, columns_list: list):
    """Deterministic answers for shapes the plan engine mishandled (see docs/PROGRESS.md)."""
    if any(re.match(p, q_lower) for p in _PURE_COUNT_PATTERNS):
        return f"SELECT COUNT(*) AS [Total_Records] FROM {safe_tbl};", ()

    # "number of unique products", "how many distinct cities": a distinct count of one
    # column. The plan engine answered these with a per-value COUNT(*) breakdown.
    m = (re.search(r"\b(?:number of|count of|how many|total)\s+(?:unique|distinct|different)\s+([a-z][a-z0-9_ ]*)", q_lower)
         or re.search(r"\b(?:unique|distinct)\s+([a-z][a-z0-9_ ]*?)\s+count\b", q_lower))
    if m:
        words = m.group(1).split()
        for n in range(min(3, len(words)), 0, -1):
            cand = "".join(words[:n])
            stems = {cand, cand[:-1] if cand.endswith("s") else cand, cand[:-2] if cand.endswith("es") else cand,
                     cand[:-3] + "y" if cand.endswith("ies") else cand}
            for col in columns_list:
                if re.sub(r"[^a-z0-9]", "", str(col).lower()) in stems:
                    safe_col = sanitize_identifier(col)
                    return (f"SELECT COUNT(DISTINCT {safe_col}) AS [Unique_{col}] FROM {safe_tbl} "
                            f"WHERE {safe_col} IS NOT NULL;"), ()

    col_text = " ".join(re.sub(r"([a-z])([A-Z])", r"\1 \2", str(c)).replace("_", " ").lower() for c in columns_list)
    # "shipping cost", "delivery fee": a specific measure that no column carries. Answering
    # with some other numeric column (e.g. UnitPrice) would be a confident wrong answer.
    for m in re.finditer(r"\b([a-z]+)\s+(" + _MEASURE_WORDS + r")\b", q_lower):
        qual, measure = m.group(1), m.group(2)
        phrase = f"{qual} {measure}"
        if qual in _GENERIC_QUALIFIERS or phrase in col_text or qual in col_text:
            continue
        if re.search(r"\b" + re.escape(measure.rstrip("s")) , col_text):
            # A column with the measure word exists but not for this qualifier.
            pass
        nums, _ = _numeric_and_date_columns(table_name, columns_list)
        listed = ", ".join(nums[:6]) if nums else ", ".join(columns_list[:6])
        return DATA_NOT_FOUND_PREFIX + f"There is no '{phrase}' column in this dataset. Numeric columns available: {listed}.", ()

    # Monthly / yearly trend of a metric: group by the dataset's date column.
    for pat, grain in _TIME_GRAIN:
        if re.search(pat, q_lower):
            nums, dates = _numeric_and_date_columns(table_name, columns_list)
            if not dates:
                break
            try:
                from ai.metric_resolver import resolve_metric_column
                metric, _ = resolve_metric_column(q_lower, nums)
            except Exception:
                metric = None
            date_col = sanitize_identifier(dates[0])
            fmt = "yyyy-MM" if grain == "month" else "yyyy"
            period = f"FORMAT(TRY_CAST({date_col} AS DATE), '{fmt}')"
            label = "Month" if grain == "month" else "Year"
            if metric:
                safe_expr = f"TRY_CAST(REPLACE(CAST({sanitize_identifier(metric)} AS NVARCHAR(MAX)), ',', '') AS FLOAT)"
                agg = "AVG" if re.search(r"\b(?:average|avg|mean|ausat)\b", q_lower) else "SUM"
                name = f"{'Average' if agg == 'AVG' else 'Total'}_{metric}"
                return (f"SELECT {period} AS [{label}], {agg}({safe_expr}) AS [{name}] FROM {safe_tbl} "
                        f"WHERE TRY_CAST({date_col} AS DATE) IS NOT NULL GROUP BY {period} ORDER BY [{label}] ASC;"), ()
            if re.search(r"\b(?:orders?|records?|rows?|count|kitne)\b", q_lower):
                return (f"SELECT {period} AS [{label}], COUNT(*) AS [Record_Count] FROM {safe_tbl} "
                        f"WHERE TRY_CAST({date_col} AS DATE) IS NOT NULL GROUP BY {period} ORDER BY [{label}] ASC;"), ()
            break
    return None


_SPELL_VOCAB = {
    "total", "average", "count", "sales", "region", "profit", "revenue", "quantity", "price", "orders",
    "customer", "customers", "product", "products", "monthly", "highest", "lowest", "compare", "category",
    "many", "much", "were", "order", "cancelled", "canceled", "delivered", "returned", "pending", "trend",
    "month", "months", "yearly", "weekly", "daily", "maximum", "minimum", "number", "which", "show",
    "status", "top", "city", "cities", "amount", "units", "sum", "least", "most", "best", "worst",
}

# Roman Urdu / Hinglish phrases that mean an analysis word (applied before spelling).
_PHRASE_NORMALIZE = [
    (r"\bmah?ine?y?\s+(?:ke|k|ki)\s+(?:hisab|hisaab|lehaz|lihaz)\s+se\b", "monthly"),
    (r"\bmah[ia]n[ae]y?\s*(?:wise|wari|war)\b", "monthly"),
    (r"\bmahana\b", "monthly"),
    (r"\bsaal\s+(?:ke|k)\s+(?:hisab|hisaab)\s+se\b", "yearly"),
    (r"\bsalana\b", "yearly"),
]

# Common Roman Urdu / Hinglish words: never "corrected" into a column or a value
# ("mera" is not "Camera").
_NEVER_CORRECT = {
    "mera", "meri", "mere", "tera", "teri", "hamara", "hamari", "unka", "uska", "iska", "kya", "kyun",
    "kaisa", "kaise", "kaisi", "karun", "karo", "kharab", "acha", "achha", "theek", "batao", "bataen",
    "dikhao", "dikha", "kitne", "kitni", "kitna", "zyada", "ziada", "kam", "sab", "sabse", "wala",
    "wali", "wale", "hain", "nahi", "nahin", "abhi", "kal", "aaj", "mausam", "please", "thanks",
}
_SHORT_FIXES = {"mny": "many", "hw": "how", "wer": "were", "hw": "how"}

# Business synonyms: used ONLY when the dataset has no column with that name, and the
# substitution is reported to the user as an assumption.
_METRIC_SYNONYMS = {
    "revenue": ("sales", "revenue", "amount", "totalsales", "total_sales", "turnover"),
    "revnue": ("sales", "revenue", "amount"),
    "income": ("sales", "revenue", "amount"),
    "turnover": ("sales", "revenue", "amount"),
    "earnings": ("profit", "netprofit", "net_profit"),
    "margin": ("profit",),
    "kamai": ("sales", "revenue", "amount"),
    "munafa": ("profit",),
}


def correct_question_with_notes(question: str, columns_list: list, table_name: str = None):
    """
    Typo-tolerant normalisation of a question (R-Phase 9.4).

    Returns (corrected_question, notes). Corrects, in order:
      1. Roman Urdu/Hinglish phrases ("mahine ke hisab se" -> "monthly").
      2. Metric synonyms when the dataset has no such column ("revenue" -> "Sales"),
         reported as an assumption note.
      3. Close misspellings of column names, analysis words and DATASET VALUES
         ("lahor" -> "Lahore", "laptp" -> "Laptop"). Each value correction is noted.
      4. A misspelling that is equally close to two different values is NOT guessed:
         it is left as typed and a note asks the user which one they meant.
    """
    import difflib
    notes = []
    q = question or ""
    for pat, rep in _PHRASE_NORMALIZE:
        q = re.sub(pat, rep, q, flags=re.IGNORECASE)

    col_lower = {str(c).lower(): str(c) for c in columns_list}
    col_parts = {}
    for c in columns_list:
        for part in re.split(r"[^A-Za-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", str(c))):
            if len(part) >= 3:
                col_parts.setdefault(part.lower(), str(c))
    vocab = set(_SPELL_VOCAB) | set(col_parts)

    values = {}
    if table_name:
        try:
            from ai.value_matcher import get_column_vocabulary
            try:
                from ai.schema_intelligence import get_dataset_schema_intelligence
                si = get_dataset_schema_intelligence(table_name)
            except Exception:
                si = None
            for col, vals in (get_column_vocabulary(table_name, si) or {}).items():
                for v in vals:
                    sv = str(v).strip()
                    if 3 <= len(sv) <= 40 and re.fullmatch(r"[A-Za-z][A-Za-z ]*", sv):
                        values.setdefault(sv.lower(), sv)
        except Exception:
            values = {}
    value_words = {}
    for low, orig in values.items():
        value_words.setdefault(low, orig)

    out = []
    for tok in re.split(r"(\W+)", q):
        low = tok.lower()
        if low in _SHORT_FIXES:
            out.append(_SHORT_FIXES[low])
            continue
        if not low.isalpha() or len(low) < 3 or low in _NEVER_CORRECT:
            out.append(tok)
            continue
        # 2. synonyms for a metric the dataset doesn't have
        if low in _METRIC_SYNONYMS and low not in col_parts and low not in col_lower:
            target = next((col_lower[c] for c in _METRIC_SYNONYMS[low] if c in col_lower), None)
            if target:
                notes.append(f"There is no '{tok}' column, so I used {target}.")
                out.append(target)
                continue
        if low in vocab or low in value_words or len(low) < 4:
            out.append(tok)
            continue
        # 3a. dataset values (city, product, status ...)
        if value_words:
            # Same first letter and at most one or two letters off: a typo, not another word.
            scored = sorted(((difflib.SequenceMatcher(None, low, v).ratio(), v) for v in value_words
                             if v[:1] == low[:1] and abs(len(v) - len(low)) <= max(1, len(low) // 4)), reverse=True)
            scored = scored or [(0.0, "")]
            best = scored[0]
            if best[0] >= 0.8 and abs(len(best[1]) - len(low)) <= 2:
                if len(scored) > 1 and scored[1][0] >= best[0] - 0.02 and scored[1][0] >= 0.8:
                    a, b = value_words[best[1]], value_words[scored[1][1]]
                    notes.append(f"'{tok}' could mean {a} or {b}; I left it as typed. Please say which one you mean.")
                    out.append(tok)
                    continue
                fixed = value_words[best[1]]
                if fixed.lower() != low:
                    notes.append(f"Read '{tok}' as {fixed}.")
                out.append(fixed)
                continue
        # 3b. columns and analysis words
        m = [w for w in difflib.get_close_matches(low, vocab, n=3, cutoff=0.75) if w[:1] == low[:1]]
        if m and abs(len(m[0]) - len(low)) <= 2:
            fix = m[0]
            if fix in col_parts:
                notes.append(f"Read '{tok}' as the {col_parts[fix]} column.")
            out.append(fix)
            continue
        out.append(tok)
    return "".join(out), notes


def correct_question_typos(question: str, columns_list: list) -> str:
    """Back-compat wrapper (columns and analysis words only, no notes)."""
    return correct_question_with_notes(question, columns_list)[0]


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

    # Priority -1: questions the plan engine has been seen to get wrong.
    early = _early_patterns(q_lower, table_name, safe_tbl, columns_list)
    if early is not None:
        return early

    # Priority 0: Structured Query Plan Engine Resolution (0.001s Local Execution)
    if parse_question_to_query_plan:
        try:
            plan = parse_question_to_query_plan(question, table_name)
            # If query plan resolved cleanly without unresolved notices, execute the generated plan
            if plan and not plan.get("unresolved") and (plan.get("limit") or plan.get("filters") or plan.get("computed_fields") or plan.get("order_by") or plan.get("intent")):
                if plan.get("intent") == "count" and not plan.get("filters"):
                    return f"SELECT COUNT(*) AS [Total_Records] FROM {safe_tbl};", ()

                select_items = []
                if plan.get("computed_fields"):
                    for cf in plan["computed_fields"]:
                        select_items.append(f"{cf['expression']} AS [{cf['name']}]")
                for sc in plan.get("select", []):
                    if sc not in [cf["name"] for cf in plan.get("computed_fields", [])]:
                        if sc.strip().upper().startswith("CASE"):
                            alias = "Sub_Area" if ("Address" in sc or "Area" in sc) else "Category"
                            select_items.append(f"{sc} AS [{alias}]")
                        elif sc.startswith("[") and sc.endswith("]"):
                            select_items.append(sc)
                        elif "(" in sc:
                            # A raw SQL expression (e.g. the FORMAT(...)/DATEPART(...) date-
                            # grain breakdown query_plan_engine builds for "by month"/"by
                            # year" follow-ups) rather than a bare column name - bracket-
                            # wrapping it like a column (f"[{sc}]") would produce invalid T-SQL.
                            select_items.append(f"{sc} AS [Period]")
                        else:
                            select_items.append(f"[{sc}]")

                # Every GROUP BY column must be in the output, otherwise grouped numbers come
                # back without the labels that say which group each one belongs to.
                for g in plan.get("group_by", []):
                    g_plain = g if (g.startswith("[") or "(" in g or g.strip().upper().startswith("CASE")) else f"[{g}]"
                    if g_plain.startswith("[") and not any(g_plain == it or it.startswith(g_plain + " ") for it in select_items):
                        select_items.append(g_plain)
                select_clause = ", ".join(select_items) if select_items else "*"
                top_clause = f"TOP ({plan['limit']}) " if plan.get("limit") else ""
                plan_params = []
                if plan.get("filters"):
                    where_clause = f" WHERE {' AND '.join([f['sql_clause'] for f in plan['filters']])}"
                    for f in plan["filters"]:
                        plan_params.extend(f.get("params", []))
                else:
                    where_clause = ""
                
                group_items = []
                for g in plan.get("group_by", []):
                    if g.strip().upper().startswith("CASE") or "(" in g:
                        group_items.append(g)
                    elif g.startswith("[") and g.endswith("]"):
                        group_items.append(g)
                    else:
                        group_items.append(f"[{g}]")
                group_clause = f" GROUP BY {', '.join(group_items)}" if group_items else ""
                having_clause = " HAVING COUNT(*) > 1" if plan.get("intent") == "duplicate_records" else ""
                order_items = [f"{o['sql_safe_column']} {o['direction']}" for o in plan.get("order_by", [])]
                order_clause = f" ORDER BY {', '.join(order_items)}" if order_items else ""

                plan_sql = f"SELECT {top_clause}{select_clause} FROM {safe_tbl}{where_clause}{group_clause}{having_clause}{order_clause};"
                return plan_sql, tuple(plan_params)
        except Exception as plan_err:
            print("[QUERY PLAN NOTICE]:", plan_err)

    # 0. Check for "Show All / Full Data / Sara Data" intent
    is_show_all = any(kw in q_lower for kw in ["all", "sara", "sare", "sab", "poora", "pura", "tamam", "entire", "full", "everything"])

    # 0. Check for "Count" / "Total rows" intent without filters (must be a standalone total count inquiry)
    pure_table_count_patterns = [
        r"^\s*(?:what\s+is\s+the\s+)?(?:total\s+)?(?:count|number\s+of)\s+(?:of\s+)?(?:records|rows|data|entries|all\s+records)\s*\??\s*$",
        r"^\s*(?:total\s+)?(?:records|rows|data)\s+(?:count|kitne\s+hain|kitni\s+hain)\s*\??\s*$",
        r"^\s*(?:how\s+many|kitne|kitni)\s+(?:total\s+)?(?:records|rows|data|entries)\s*(?:are\s+there|hain)?\s*\??\s*$"
    ]
    if any(re.match(pat, q_lower) for pat in pure_table_count_patterns):
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
        stop_words = {
            "what", "where", "when", "which", "who", "whom", "whose", "why", "how", "this", "that", "these", "those",
            "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
            "my", "your", "his", "her", "its", "our", "their", "the", "a", "an", "and", "or", "but",
            "in", "on", "at", "to", "for", "of", "with", "by", "from", "up", "about", "into", "over", "after",
            # Conversational & Roman Urdu
            "jahan", "kahan", "aaye", "aaya", "aayi", "gaye", "gaya", "gayi", "hua", "hue", "hui",
            "kaun", "kon", "kis", "kise", "dikhao", "batao", "btao", "karo", "karon", "karein", "rakha", "rakhi",
            "mera", "meri", "mere", "hamara", "hamari", "hamare", "paas", "pass", "se", "ko", "ka", "ki", "ke",
            "mein", "main", "par", "pe", "hai", "hain", "tha", "the", "thi", "mujhe", "mujha",
            # Analytical generic keywords
            "area", "wise", "hisab", "show", "give", "list", "fetch", "return", "returns", "orders", "data", "records", "rows", "total", "count"
        }
        tokens = [t for t in target_entity.split() if len(t) >= 3 and t.lower() not in stop_words]
        if tokens and len(tokens) <= 2:
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


def ask_gemini(prompt: str, max_tokens: int = 550, timeout: float = 2.5, preferred_model: str = None) -> tuple:
    """
    Send prompt to OpenRouter AI API using ultra-fast model fallback loop for sub-second response times.

    If preferred_model is a known FAST_MODELS entry, it is tried FIRST; on failure/timeout
    the normal FAST_MODELS order is still tried as a fallback (minus the preferred model,
    already attempted). This is what lets a user's saved "AI model" preference (Enterprise
    Settings page) actually influence which model answers their question, without ever
    leaving them with no answer if that specific model happens to be down.

    Returns (text, model_used) - model_used is None if every model failed (caller falls
    back to its own non-LLM behavior), so callers/tests can show which model actually
    answered instead of just trusting the call "worked".
    """
    if not client or not api_key:
        return "", None

    cache_key = f"ai_prompt_{hash(prompt)}_{max_tokens}_{preferred_model or ''}"
    if system_cache:
        cached_val = system_cache.get(cache_key)
        if cached_val:
            return cached_val

    models_to_try = list(FAST_MODELS)
    if preferred_model and preferred_model in FAST_MODELS:
        models_to_try.remove(preferred_model)
        models_to_try.insert(0, preferred_model)

    # The OpenAI SDK's own `timeout=` kwarg has already been measured, in this exact
    # codebase, to NOT reliably bound a call's real wall-clock time (gpt-4o-mini observed
    # taking 25.86s against a stated 10s timeout in earlier testing) - almost certainly a
    # connection-setup/retry cost inside the SDK/httpx layer that the timeout parameter
    # doesn't fully cover. Live profiling for this pass found the same pattern driving a
    # ~12s first-question Ask AI response: generate_data_business_summary's explanation
    # call (timeout=2.0) working through all 4 FAST_MODELS in turn, each one genuinely
    # costing multiple real seconds despite the stated budget. A daemon Thread+Queue hard
    # timeout (the same pattern already used for phrase_proactive_insight/
    # phrase_why_investigation, for the identical reason) can be abandoned the instant the
    # deadline passes, regardless of what the SDK call is actually doing underneath.
    import threading, queue as _queue

    for model_name in models_to_try:
        result_q = _queue.Queue(maxsize=1)

        def _call_model(_model_name=model_name):
            try:
                response = client.chat.completions.create(
                    model=_model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    timeout=timeout
                )
                if response and response.choices:
                    text = response.choices[0].message.content or ""
                    result_q.put(text)
                else:
                    result_q.put(None)
            except Exception:
                result_q.put(None)

        threading.Thread(target=_call_model, daemon=True).start()
        try:
            result = result_q.get(timeout=timeout + 0.5)
        except _queue.Empty:
            continue

        if result and result.strip():
            if system_cache:
                system_cache.set(cache_key, (result, model_name), ttl=1800)
            return result, model_name

    return "", None


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


def _not_understood_message(columns_list: list) -> str:
    numeric = [c for c in columns_list if re.search(r"(?i)sales|amount|price|revenue|profit|cost|total|qty|quantity|value", str(c))]
    groups = [c for c in columns_list if re.search(r"(?i)region|city|country|category|product|status|type|segment|department", str(c))]
    metric = numeric[0] if numeric else (columns_list[1] if len(columns_list) > 1 else "value")
    group = groups[0] if groups else (columns_list[0] if columns_list else "category")
    return NOT_UNDERSTOOD_MESSAGE.format(metric=metric, group=group)


def _not_understood_result(columns_list: list, start_time: float) -> dict:
    return {
        "success": False, "is_out_of_domain": False, "is_data_not_found": True, "is_fast_pattern": False,
        "sql": None, "df": pd.DataFrame(), "rows_returned": 0,
        "execution_time_ms": round((time.time() - start_time) * 1000, 2),
        "confidence": 0.0, "retries": 0, "error": _not_understood_message(columns_list),
    }


def generate_sql(question: str, table_name: str, columns_list: list, preferred_model: str = None) -> tuple:
    """
    Generate T-SQL query using Fast Local Pattern Engine or OpenRouter AI API with caching.
    Returns (sql_query, params, is_fast_pattern, model_used). params is always a tuple
    (possibly empty); any user-derived literal value in a fast-pattern query is bound as a
    parameter, never string-concatenated into the SQL text. model_used is None whenever the
    fast deterministic pattern engine produced the SQL (the normal, common case) - it's only
    set when the LLM actually had to write the SQL itself.
    """
    cache_key = f"sql_gen_{table_name}_{hash(question)}"
    if system_cache:
        cached = system_cache.get(cache_key)
        if cached:
            cached_sql, cached_params = cached
            try:
                test_df = run_query(cached_sql, cached_params or None)
                if test_df is not None and not test_df.empty:
                    return cached_sql, cached_params, True, None
            except Exception:
                pass

    # Try 0.001s Fast Pattern Generator first
    fast_result = fast_pattern_sql_generator(question, table_name, columns_list)
    if fast_result:
        fast_sql, fast_params = fast_result
        if system_cache:
            system_cache.set(cache_key, (fast_sql, fast_params), ttl=1800)
        return fast_sql, fast_params, True, None

    columns_text = "\n".join(columns_list)
    sample_data_text = extract_sample_data_text(table_name)

    prompt = generate_sql_prompt(question, table_name, columns_text, sample_data_text)
    raw_response, model_used = ask_gemini(prompt, preferred_model=preferred_model)
    cleaned_sql = clean_sql_response(raw_response)

    if cleaned_sql == UNRELATED_MESSAGE or (raw_response and UNRELATED_MESSAGE[:40] in raw_response):
        # The model itself judged the question unrelated to the dataset: refuse, do not
        # silently run a SELECT * and present the rows as an answer.
        return UNRELATED_MESSAGE, (), False, model_used
    if not cleaned_sql:
        return DATA_NOT_FOUND_PREFIX + _not_understood_message(columns_list), (), False, model_used

    if system_cache and cleaned_sql:
        system_cache.set(cache_key, (cleaned_sql, ()), ttl=1800)

    return cleaned_sql, (), False, model_used


def fix_sql(question: str, table_name: str, columns_text: str, invalid_sql: str, error_message: str, preferred_model: str = None) -> tuple:
    """
    Repair invalid SQL using error feedback loop. Returns (cleaned_sql, model_used).
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

    raw_response, model_used = ask_gemini(fix_prompt, preferred_model=preferred_model)
    return clean_sql_response(raw_response), model_used


def execute_sql_with_retry(question: str, table_name: str, columns_list: list, max_retries: int = 3, preferred_model: str = None) -> dict:
    """
    Execute AI SQL generation with domain guardrails, fast local engine, and auto-retry loop.

    preferred_model: a user's saved "AI model" preference (Enterprise Settings page), tried
    first whenever the LLM actually needs to be called (the fast deterministic pattern engine
    is always tried first regardless, per the project's design - the preference only affects
    which model is asked if/when that deterministic path can't handle the question).
    """
    start_time = time.time()
    question, typo_notes = correct_question_with_notes(question, columns_list, table_name)

    # Enforce strict domain guardrails
    if is_out_of_domain_question(question, columns_list, table_name_hint=table_name):
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

    sql, params, is_fast_pattern, model_used = generate_sql(question, table_name, columns_list, preferred_model=preferred_model)

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

    if isinstance(sql, str) and sql.startswith(DATA_NOT_FOUND_PREFIX):
        notice = sql[len(DATA_NOT_FOUND_PREFIX):]
        return {
            "success": False,
            "is_out_of_domain": False,
            "is_data_not_found": True,
            "is_fast_pattern": False,
            "sql": None,
            "df": pd.DataFrame(),
            "rows_returned": 0,
            "execution_time_ms": round((time.time() - start_time) * 1000, 2),
            "confidence": 0.0,
            "retries": 0,
            "error": notice
        }

    if not sql:
        return _not_understood_result(columns_list, start_time)

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
                "assumptions": typo_notes,
                "interpreted_question": question,
                "sql": sql,
                "df": df,
                "chart": None,
                "chart_spec": chart_spec,
                "rows_returned": len(df),
                "execution_time_ms": execution_time_ms,
                "confidence": confidence,
                "retries": retry_count,
                "model_used": model_used,
                "error": None
            }

        except Exception as e:
            last_error = str(e)
            retry_count += 1
            if retry_count <= max_retries:
                # fix_sql() always produces a fresh, fully self-contained SQL string
                # (no bound placeholders), so params must be reset alongside it.
                sql, fix_model_used = fix_sql(question, table_name, columns_text, sql, last_error, preferred_model=preferred_model)
                if fix_model_used:
                    model_used = fix_model_used
                params = ()
                if not sql:
                    break
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
        "model_used": model_used,
        "error": last_error
    }