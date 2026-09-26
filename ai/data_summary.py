import pandas as pd


def generate_business_summary(df: pd.DataFrame) -> dict:
    """
    Generate comprehensive business summary metrics for any DataFrame.
    """
    if df is None or df.empty:
        return {
            "Total Rows": 0,
            "Total Columns": 0,
            "Numeric Columns": 0,
            "Text Columns": 0,
            "Date Columns": 0,
            "Missing Values": 0,
            "Duplicate Rows": 0,
            "Memory Usage (MB)": 0.0
        }

    numeric_cols = df.select_dtypes(include="number")
    object_cols = df.select_dtypes(include="object")
    datetime_cols = df.select_dtypes(include="datetime")

    memory_mb = round(df.memory_usage(deep=True).sum() / (1024 * 1024), 2)

    summary = {
        "Total Rows": len(df),
        "Total Columns": len(df.columns),
        "Numeric Columns": len(numeric_cols.columns),
        "Text Columns": len(object_cols.columns),
        "Date Columns": len(datetime_cols.columns),
        "Missing Values": int(df.isnull().sum().sum()),
        "Duplicate Rows": int(df.duplicated().sum()),
        "Memory Usage (MB)": memory_mb
    }

    if not numeric_cols.empty:
        summary["Maximum Value"] = float(numeric_cols.max().max())
        summary["Minimum Value"] = float(numeric_cols.min().min())
        summary["Average Value"] = round(float(numeric_cols.mean().mean()), 2)
    else:
        summary["Maximum Value"] = "N/A"
        summary["Minimum Value"] = "N/A"
        summary["Average Value"] = "N/A"

    return summary


def summarize_dataset(df: pd.DataFrame) -> str:
    """
    Format business summary into a clean readable string.
    """
    summary = generate_business_summary(df)
    output = ["========================================", "📊 BUSINESS SUMMARY", "========================================"]
    for key, value in summary.items():
        output.append(f"{key:<22}: {value}")
    return "\n".join(output)


def format_ai_explanation(text: str) -> str:
    """
    Transform raw AI explanation markdown text into executive-level HTML blocks
    with icons, metric pills, callout boxes, and bold highlights.
    """
    import re
    if not text or not isinstance(text, str):
        return ""

    # Split double line breaks into paragraphs
    sections = [s.strip() for s in text.split("\n\n") if s.strip()]
    formatted_blocks = []

    for section in sections:
        lines = [l.strip() for l in section.split("\n") if l.strip()]
        if not lines:
            continue

        header_line = lines[0]
        body_lines = lines[1:] if len(lines) > 1 else []

        # Strip leading Markdown header hashes (e.g. ###, ##, #) from header line
        header_text_clean = re.sub(r"^[#\s]+", "", header_line).strip()

        # Check section type
        if "📊" in header_line or "Data Scope" in header_text_clean or "Executive Query Summary" in header_text_clean:
            clean_head = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", header_text_clean).replace("📊", "").strip()
            clean_body = "<br>".join([re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", l) for l in body_lines])
            formatted_blocks.append(f"""
            <div style="background: rgba(37,99,235,0.06); border: 1px solid rgba(37,99,235,0.2); border-left: 4px solid var(--accent-blue); padding: 16px 20px; border-radius: var(--radius-md); margin-bottom: 16px; box-shadow: var(--shadow-sm);">
                <div style="font-weight: 800; font-size: 15px; color: var(--accent-blue); margin-bottom: 6px; display: flex; align-items: center; gap: 8px;">
                    <i class="fa-solid fa-chart-pie" style="font-size: 16px;"></i> {clean_head}
                </div>
                {"<div style='font-size: 14px; color: var(--text-primary); line-height: 1.6;'>" + clean_body + "</div>" if clean_body else ""}
            </div>
            """)

        elif "📝" in header_line or "Record Synthesis" in header_text_clean or "Key Findings" in header_text_clean or "Multi-Concept" in header_text_clean:
            clean_head = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", header_text_clean).replace("📝", "").strip()
            bullet_html = ""
            for l in body_lines:
                clean_l = re.sub(r"^[•\-\s]+", "", l)
                clean_l = re.sub(r"\*\*(.*?)\*\*", r"<strong style='color: #6366f1;'>\1</strong>", clean_l)
                bullet_html += f"""
                <div style="display: flex; align-items: flex-start; gap: 10px; margin-bottom: 6px; padding: 8px 12px; background: rgba(99,102,241,0.05); border-radius: var(--radius-sm); border: 1px solid rgba(99,102,241,0.2);">
                    <i class="fa-solid fa-list-check" style="color: #6366f1; margin-top: 4px; font-size: 12px;"></i>
                    <div style="font-size: 13.5px; color: var(--text-primary); flex: 1;">{clean_l}</div>
                </div>
                """
            formatted_blocks.append(f"""
            <div style="background: var(--bg-surface); border: 1px solid var(--border-color); border-left: 4px solid #6366f1; padding: 16px 20px; border-radius: var(--radius-md); margin-bottom: 16px;">
                <div style="font-weight: 800; font-size: 15px; color: #6366f1; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">
                    <i class="fa-solid fa-square-poll-vertical" style="font-size: 16px;"></i> {clean_head}
                </div>
                {bullet_html}
            </div>
            """)

        elif "🔍" in header_line or "Dimensions" in header_text_clean or "Category" in header_text_clean or "Distribution" in header_text_clean:
            clean_head = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", header_text_clean).replace("🔍", "").strip()
            bullet_html = ""
            for l in body_lines:
                clean_l = re.sub(r"^[•\-\s]+", "", l)
                clean_l = re.sub(r"\*\*(.*?)\*\*", r"<strong style='color: var(--accent-purple);'>\1</strong>", clean_l)
                bullet_html += f"""
                <div style="display: flex; align-items: flex-start; gap: 10px; margin-bottom: 6px; padding: 6px 12px; background: var(--bg-surface); border-radius: var(--radius-sm); border: 1px solid var(--border-color);">
                    <i class="fa-solid fa-magnifying-glass" style="color: var(--accent-purple); margin-top: 4px; font-size: 11px;"></i>
                    <div style="font-size: 13.5px; color: var(--text-primary); flex: 1;">{clean_l}</div>
                </div>
                """
            formatted_blocks.append(f"""
            <div style="background: var(--bg-primary); border: 1px solid var(--border-color); border-left: 4px solid var(--accent-purple); padding: 16px 20px; border-radius: var(--radius-md); margin-bottom: 16px;">
                <div style="font-weight: 800; font-size: 15px; color: var(--accent-purple); margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">
                    <i class="fa-solid fa-layer-group" style="font-size: 16px;"></i> {clean_head}
                </div>
                {bullet_html}
            </div>
            """)

        elif "📈" in header_line or "Metrics" in header_text_clean or "Totals" in header_text_clean or "Count" in header_text_clean:
            clean_head = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", header_text_clean).replace("📈", "").strip()
            metric_pills = ""
            for l in body_lines:
                clean_l = re.sub(r"^[•\-\s]+", "", l)
                clean_l = re.sub(r"\*\*(.*?)\*\*", r"<strong style='color: var(--accent-green);'>\1</strong>", clean_l)
                metric_pills += f"""
                <div style="display: flex; align-items: flex-start; gap: 10px; margin-bottom: 6px; padding: 8px 14px; background: rgba(16,185,129,0.06); border-radius: var(--radius-sm); border: 1px solid rgba(16,185,129,0.2);">
                    <i class="fa-solid fa-arrow-trend-up" style="color: var(--accent-green); margin-top: 4px; font-size: 12px;"></i>
                    <div style="font-size: 13.5px; color: var(--text-primary); flex: 1;">{clean_l}</div>
                </div>
                """
            formatted_blocks.append(f"""
            <div style="background: var(--bg-surface); border: 1px solid var(--border-color); border-left: 4px solid var(--accent-green); padding: 16px 20px; border-radius: var(--radius-md); margin-bottom: 16px;">
                <div style="font-weight: 800; font-size: 15px; color: var(--accent-green); margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">
                    <i class="fa-solid fa-calculator" style="font-size: 16px;"></i> {clean_head}
                </div>
                {metric_pills}
            </div>
            """)

        elif "💡" in header_line or "Insight" in header_text_clean or "Takeaway" in header_text_clean or "Recommendation" in header_text_clean:
            clean_head = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", header_text_clean).replace("💡", "").strip()
            clean_body = "<br>".join([re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", l) for l in body_lines])
            formatted_blocks.append(f"""
            <div style="background: rgba(245,158,11,0.06); border: 1px solid rgba(245,158,11,0.25); border-left: 4px solid var(--accent-amber); padding: 16px 20px; border-radius: var(--radius-md); margin-bottom: 16px;">
                <div style="font-weight: 800; font-size: 15px; color: var(--accent-amber); margin-bottom: 6px; display: flex; align-items: center; gap: 8px;">
                    <i class="fa-solid fa-lightbulb" style="font-size: 16px;"></i> {clean_head}
                </div>
                {"<div style='font-size: 14px; color: var(--text-primary); line-height: 1.6;'>" + clean_body + "</div>" if clean_body else ""}
            </div>
            """)

        else:
            # General Markdown block fallback & markdown table parser
            if "|" in section and "-|-" in section:
                # Markdown table detected
                table_lines = [l.strip() for l in lines if "|" in l]
                if len(table_lines) >= 2:
                    header_cells = [c.strip() for c in table_lines[0].split("|") if c.strip()]
                    rows_html = []
                    for row_l in table_lines[2:]:
                        cells = [c.strip() for c in row_l.split("|") if c.strip()]
                        if cells:
                            row_tds = "".join([f"<td style='padding: 8px 14px; border-bottom: 1px solid var(--border-color); font-size: 13px;'>{re.sub(r'\\*\\*(.*?)\\*\\*', r'<strong>\\1</strong>', c)}</td>" for c in cells])
                            rows_html.append(f"<tr>{row_tds}</tr>")
                    
                    head_ths = "".join([f"<th style='padding: 10px 14px; background: var(--bg-primary); border-bottom: 2px solid var(--accent-blue); font-size: 12px; font-weight: 800; color: var(--accent-blue); text-transform: uppercase;'>{re.sub(r'\\*\\*(.*?)\\*\\*', r'<strong>\\1</strong>', c)}</th>" for c in header_cells])
                    table_markup = f"""
                    <div style="overflow-x: auto; margin: 12px 0;">
                        <table style="width: 100%; border-collapse: collapse; background: var(--bg-surface); border: 1px solid var(--border-color); border-radius: 8px;">
                            <thead><tr>{head_ths}</tr></thead>
                            <tbody>{"".join(rows_html)}</tbody>
                        </table>
                    </div>
                    """
                    formatted_blocks.append(table_markup)
                    continue

            block_html = []
            for line in lines:
                clean_l = re.sub(r"\*\*(.*?)\*\*", r"<strong style='color: var(--accent-blue);'>\1</strong>", line)
                clean_l = re.sub(r"\*(.*?)\*", r"<em>\1</em>", clean_l)
                clean_l = re.sub(r"`(.*?)`", r"<code style='background: var(--bg-primary); padding: 2px 6px; border-radius: 4px; color: var(--accent-blue);'>\1</code>", clean_l)
                if line.startswith("###") or line.startswith("##") or line.startswith("#"):
                    head_txt = line.lstrip("#").strip()
                    block_html.append(f"<h4 style='font-size: 16px; font-weight: 800; color: var(--accent-blue); margin: 16px 0 10px 0;'>{head_txt}</h4>")
                elif line.startswith("•") or line.startswith("-") or line.startswith("*"):
                    clean_item = clean_l.lstrip("•-* ").strip()
                    block_html.append(f"""
                    <div style="display: flex; align-items: flex-start; gap: 10px; margin-bottom: 6px; padding: 8px 12px; background: var(--bg-primary); border-radius: var(--radius-sm); border: 1px solid var(--border-color);">
                        <i class="fa-solid fa-circle-check" style="color: var(--accent-blue); margin-top: 4px; font-size: 12px;"></i>
                        <div style="font-size: 13.5px; color: var(--text-primary); flex: 1;">{clean_item}</div>
                    </div>
                    """)
                else:
                    block_html.append(f"<div style='margin-bottom: 6px; font-size: 14px; line-height: 1.6;'>{clean_l}</div>")

            formatted_blocks.append(f"<div style='margin-bottom: 14px;'>{''.join(block_html)}</div>")

    return "".join(formatted_blocks)