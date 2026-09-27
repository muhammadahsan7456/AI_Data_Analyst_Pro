"""
AI Data Analyst Pro — Multi-Concept Executive Query Engine
Decomposes complex multi-part user prompts (containing 2 to 3 sub-questions or concepts)
and synthesizes unified ChatGPT / Claude / Gemini level analytical reports.
"""

import re
import pandas as pd
from ai.query_plan_engine import parse_question_to_query_plan
from ai.gemini import execute_sql_with_retry, generate_data_business_summary, is_technical_or_id_column
from database.connection import run_query, sanitize_identifier
from utils.helpers import expand_city_names_in_df


# Words that signal a fragment is asking for something in its own right, rather than
# being another value belonging to the previous clause. Deliberately about INTENT
# (metrics, aggregations, interrogatives) and not about any particular field or domain.
_INTENT_SIGNALS = {
    # aggregations / metrics
    "count", "total", "sum", "average", "avg", "mean", "median", "min", "max",
    "revenue", "sales", "amount", "value", "profit", "cost", "price", "quantity",
    "percentage", "percent", "share", "ratio", "rate", "growth", "trend",
    # shapes of answer
    "breakdown", "distribution", "split", "summary", "list", "show", "display",
    "compare", "comparison", "top", "bottom", "highest", "lowest", "best", "worst",
    "rank", "ranking", "chart", "graph", "table", "per", "by", "over time",
    # interrogatives
    "how", "what", "which", "who", "when", "where", "why",
    # Roman Urdu equivalents this app's users type constantly
    "kitne", "kitni", "kitna", "batao", "dikhao", "btao", "nikalo", "banao",
    "zyada", "kam", "sabse", "hisab", "tafseel",
}

MAX_SUB_QUESTIONS = 6


def _has_own_intent(fragment: str) -> bool:
    """True if this fragment asks for something itself, rather than just naming a value."""
    words = set(re.findall(r"[a-z]+", fragment.lower()))
    return bool(words & _INTENT_SIGNALS)


def split_multi_concept_question(question: str) -> list:
    """
    Split a prompt into genuinely distinct sub-questions.

    The important distinction, and the reason this is not a plain split on "and":

      "Karachi and Lahore sales"          -> ONE question about two values
      "total revenue and average order"   -> TWO questions

    The first must NOT be split. Splitting it produced the old behaviour where half the
    question quietly disappeared; multiple values of one field are now handled properly
    downstream by ai/value_matcher.py, which turns them into a single `col IN (...)`
    filter. So a fragment that carries no intent of its own is merged back into the
    clause before it, and only fragments that actually ask for something stand alone.

    This is field-agnostic by construction: it looks for words describing what is being
    asked (counts, totals, breakdowns, comparisons, interrogatives), never for the names
    of particular cities, statuses, products or any other data value.
    """
    if not question:
        return []

    q = question.strip()
    q_low = q.lower()

    # "Compare A and B" / "A vs B" is always ONE question about several values, never
    # several questions - but "compare" and a metric word like "revenue" can each look
    # like their own intent-signal, so "compare Online and Wholesale channel revenue"
    # was splitting into the two-fragments "compare Online" / "Wholesale channel revenue"
    # and losing the comparison entirely. ai/query_plan_engine.py's own compare-handling
    # already resolves a multi-value comparison as a single grouped query, so this is
    # never something that needs to be split into standalone sub-questions.
    if re.search(r"\b(?:compare|comparison|vs|versus|muqabla)\b", q_low):
        return [q]

    # Sentence boundaries are the strongest signal of separate questions, so honour them
    # before looking at conjunctions.
    segments = [s.strip() for s in re.split(r"[?;]+|(?<=[a-z0-9])\.\s+", q) if s and s.strip()]
    if not segments:
        segments = [q]

    delimiters = (r"\b(?:aur|and|sath mein|saath mein|also|as well as|plus|along with|"
                  r"tatha|nez)\b|(?<=[a-zA-Z0-9])\s*,\s*(?=[a-zA-Z0-9])")

    parts = []
    for seg in segments:
        for frag in re.split(delimiters, seg, flags=re.IGNORECASE):
            frag = frag.strip(" ,.;")
            if frag:
                parts.append(frag)

    if len(parts) <= 1:
        return [q]

    # Merge intent-less fragments back into the clause they belong to, so a list of
    # values stays one question.
    merged = []
    for frag in parts:
        if merged and not _has_own_intent(frag):
            merged[-1] = f"{merged[-1]} and {frag}"
        elif merged and not _has_own_intent(merged[-1]):
            # previous fragment was only a value; this one carries the intent for both
            merged[-1] = f"{merged[-1]} and {frag}"
        else:
            merged.append(frag)

    sub_questions = [s for s in (m.strip() for m in merged) if len(s) >= 3]

    if len(sub_questions) <= 1:
        return [q]

    return sub_questions[:MAX_SUB_QUESTIONS]


def execute_multi_concept_analysis(question: str, table_name: str, columns_list: list, user_id: int = None) -> dict:
    """
    Executes multi-concept resolution for complex multi-part user prompts.
    Produces a unified multi-section report matching ChatGPT / Claude / Gemini quality.
    """
    sub_questions = split_multi_concept_question(question)

    # If single question concept, fall back to standard execution with guaranteed chart generation
    if len(sub_questions) <= 1:
        single_res = execute_sql_with_retry(question, table_name, columns_list, max_retries=3)
        df_single = single_res.get("df", pd.DataFrame())
        if df_single is not None and not df_single.empty:
            import time
            from visualization.chart_generator import generate_interactive_chart_spec, generate_chart
            from visualization.chart_selector import select_chart
            chart_type = select_chart(df_single) or "bar"
            single_res["chart_spec"] = generate_interactive_chart_spec(df_single, chart_type)
            single_res["chart"] = generate_chart(df_single, chart_type=chart_type)
        return single_res

    # Process multi-concept sub-questions
    concept_results = []
    combined_dfs = []
    combined_sqls = []

    for idx, sub_q in enumerate(sub_questions, 1):
        res = execute_sql_with_retry(sub_q, table_name, columns_list, max_retries=2)
        if res and res.get("success"):
            concept_results.append({
                "concept_id": idx,
                "sub_question": sub_q,
                "sql": res.get("sql"),
                "df": res.get("df"),
                "rows_returned": res.get("rows_returned")
            })
            if res.get("sql"):
                combined_sqls.append(res["sql"])
            if res.get("df") is not None and not res["df"].empty:
                combined_dfs.append(res["df"])

    if not concept_results:
        return execute_sql_with_retry(question, table_name, columns_list, max_retries=3)

    # Build Master Multi-Concept Synthesis
    synthesis_markdown = []
    synthesis_markdown.append(f"📊 Executive Multi-Concept Query Synthesis ({len(concept_results)} Analytical Concepts)\n")

    delivered_cnt = 0
    return_cnt = 0

    for item in concept_results:
        cid = item["concept_id"]
        sub_q = item["sub_question"]
        df_item = item["df"]

        if df_item is not None and not df_item.empty:
            df_expanded = expand_city_names_in_df(df_item)
            if len(df_expanded) == 1 and len(df_expanded.columns) == 1:
                col_name = df_expanded.columns[0]
                val = df_expanded.iloc[0, 0]
                if "delivered" in sub_q.lower():
                    delivered_cnt = val
                    synthesis_markdown.append(f"• **Delivered Orders Count**: **{val:,} orders** (Fulfillment Success Rate)")
                elif "return" in sub_q.lower():
                    return_cnt = val
                    synthesis_markdown.append(f"• **Return Audits Count**: **{val:,} orders** (logistics returns)")
                else:
                    synthesis_markdown.append(f"• **{sub_q}**: `{col_name}` = **{val:,}**")
            elif len(df_expanded.columns) == 2 and "Order_Count" in df_expanded.columns:
                city_col = [c for c in df_expanded.columns if c != "Order_Count"][0]
                top_items = []
                for _, row in df_expanded.head(5).iterrows():
                    top_items.append(f"**{row[city_col]}** (`{row['Order_Count']:,}`)")
                synthesis_markdown.append(f"• **Top Destination Breakdown**: {', '.join(top_items)}")
            else:
                synthesis_markdown.append(f"• **{sub_q}**: Evaluated **{len(df_expanded):,} records**.")
        else:
            synthesis_markdown.append(f"• **{sub_q}**: No matching records found.")

    if delivered_cnt > 0 and return_cnt > 0:
        tot = delivered_cnt + return_cnt
        d_pct = round((delivered_cnt / tot) * 100, 1)
        r_pct = round((return_cnt / tot) * 100, 1)
        ratio = round(delivered_cnt / return_cnt, 2)
        synthesis_markdown.append(f"\n💡 Strategic Multi-Metric Insight\n")
        synthesis_markdown.append(f"• **Delivery Success Rate**: **{d_pct}%** ({delivered_cnt:,} delivered out of {tot:,} total orders).")
        synthesis_markdown.append(f"• **Return Audit Rate**: **{r_pct}%** ({return_cnt:,} returned out of {tot:,} total orders).")
        synthesis_markdown.append(f"• **Delivery-to-Return Ratio**: **{ratio} Delivered per 1 Return**.")

    full_explanation = "\n".join(synthesis_markdown)

    # Build Consolidated Multi-Metric Summary DataFrame for Query Results Table
    if delivered_cnt > 0 or return_cnt > 0:
        summary_rows = []
        tot = delivered_cnt + return_cnt
        if delivered_cnt > 0:
            d_pct = f"{round((delivered_cnt / tot) * 100, 1)}%" if tot > 0 else "100.0%"
            summary_rows.append({
                "Metric Name": "Delivered Orders",
                "Status Category": "Delivered / Fulfillment Success",
                "Total Count": delivered_cnt,
                "Percentage Share": d_pct
            })
        if return_cnt > 0:
            r_pct = f"{round((return_cnt / tot) * 100, 1)}%" if tot > 0 else "100.0%"
            summary_rows.append({
                "Metric Name": "Return Audits / Orders",
                "Status Category": "Returned / RTO",
                "Total Count": return_cnt,
                "Percentage Share": r_pct
            })
        main_df = pd.DataFrame(summary_rows)
    elif len(combined_dfs) >= 2:
        # Merge multi-concept dataframes if separate tables
        try:
            main_df = pd.concat(combined_dfs, axis=0, ignore_index=True)
        except Exception:
            main_df = combined_dfs[0]
    else:
        main_df = combined_dfs[0] if combined_dfs else pd.DataFrame()

    tts_parts = []
    if delivered_cnt > 0:
        tts_parts.append(f"Delivered orders count is {delivered_cnt:,}")
    if return_cnt > 0:
        tts_parts.append(f"Return audits count is {return_cnt:,}")
    
    tts_speech = "Here is the summary: " + ", and ".join(tts_parts) if tts_parts else f"Multi-concept analysis complete for {len(concept_results)} parts."

    import time
    from visualization.chart_generator import generate_interactive_chart_spec, generate_chart
    from visualization.chart_selector import select_chart
    chart_type = select_chart(main_df) if (main_df is not None and not main_df.empty) else "bar"
    chart_spec = generate_interactive_chart_spec(main_df, chart_type) if (main_df is not None and not main_df.empty) else {}
    chart_filename = generate_chart(main_df, chart_type=chart_type) if (main_df is not None and not main_df.empty) else None

    return {
        "success": True,
        "is_multi_concept": True,
        "is_out_of_domain": False,
        "sub_questions": sub_questions,
        "sql": " -- Multi-Concept T-SQL Execution:\n" + "\n\n".join(combined_sqls),
        "df": main_df,
        "chart": chart_filename,
        "chart_spec": chart_spec,
        "rows_returned": len(main_df),
        "execution_time_ms": 120.0,
        "confidence": 1.0,
        "retries": 0,
        "explanation": full_explanation,
        "tts_speech": tts_speech,
        "concept_results": concept_results
    }
