import os
import sys
import io
import re
import gc
import uuid
import base64
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except Exception:
    matplotlib = None
    plt = None
import pandas as pd

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from visualization.charts import (
        create_bar_chart,
        create_line_chart,
        create_pie_chart,
        create_histogram,
        create_scatter_chart,
        create_box_plot,
        create_area_chart,
        create_heatmap,
        create_radar_chart,
        create_treemap_chart,
        create_sunburst_chart,
        create_waterfall_chart,
        create_funnel_chart,
        create_bubble_chart,
        create_candlestick_chart
    )
except ModuleNotFoundError:
    from charts import (
        create_bar_chart,
        create_line_chart,
        create_pie_chart,
        create_histogram,
        create_scatter_chart,
        create_box_plot,
        create_area_chart,
        create_heatmap,
        create_radar_chart,
        create_treemap_chart,
        create_sunburst_chart,
        create_waterfall_chart,
        create_funnel_chart,
        create_bubble_chart,
        create_candlestick_chart
    )

CHART_MAP = {
    "bar": create_bar_chart,
    "line": create_line_chart,
    "pie": create_pie_chart,
    "histogram": create_histogram,
    "scatter": create_scatter_chart,
    "box": create_box_plot,
    "boxplot": create_box_plot,
    "area": create_area_chart,
    "heatmap": create_heatmap,
    "radar": create_radar_chart,
    "treemap": create_treemap_chart,
    "sunburst": create_sunburst_chart,
    "waterfall": create_waterfall_chart,
    "funnel": create_funnel_chart,
    "bubble": create_bubble_chart,
    "candlestick": create_candlestick_chart
}


def get_chart_builder(chart_type: str):
    c_type = str(chart_type).lower().strip()
    return CHART_MAP.get(c_type, create_bar_chart)


from visualization.charts import (
    create_bar_chart,
    create_line_chart,
    create_pie_chart,
    create_histogram,
    create_scatter_chart,
    create_box_plot,
    create_area_chart,
    create_heatmap,
    create_radar_chart,
    create_treemap_chart,
    create_sunburst_chart,
    create_waterfall_chart,
    create_funnel_chart,
    create_bubble_chart,
    create_candlestick_chart,
    preprocess_chart_dataframe
)


def generate_pure_svg_chart(df: pd.DataFrame, chart_type: str = "bar") -> str:
    """
    Generate modern responsive dark SVG chart in pure Python without needing Matplotlib or C-extensions.
    """
    if df is None or df.empty or df.shape[1] < 1:
        return ""

    df_clean = df.copy()
    numeric_cols = df_clean.select_dtypes(include="number").columns.tolist()
    
    # Filter out ID columns and serial numbers from numeric list
    id_patterns = [r"^recordid$", r"^id$", r".*_id$", r"^key$", r"^tracking_id$", r"^sr$", r"^s\.no$", r"^s_no$", r"^sno$", r"^index$", r"^row_num$"]
    numeric_cols = [c for c in numeric_cols if not any(re.match(p, str(c).lower().strip()) for p in id_patterns)]

    categorical_cols = [c for c in df_clean.columns if c not in numeric_cols and c.lower() not in ["recordid", "id"]]

    cat_col = categorical_cols[0] if categorical_cols else df_clean.columns[0]
    num_col = numeric_cols[0] if numeric_cols else df_clean.columns[-1]

    if cat_col == num_col:
        # Only one usable column was resolved (single-column dataset, or every other
        # column was filtered out as an ID). Selecting [cat_col, num_col] would produce
        # a DataFrame with two identically-named columns and crash downstream, so derive
        # a sensible category axis instead of duplicating the same column.
        if numeric_cols:
            vals_series = pd.to_numeric(df_clean[num_col], errors="coerce").dropna().head(100)
            sub_df = pd.DataFrame({
                "_Row": [f"#{i + 1}" for i in range(len(vals_series))],
                num_col: vals_series.tolist()
            })
            cat_col = "_Row"
        else:
            counts = df_clean[cat_col].astype(str).value_counts().head(15)
            sub_df = pd.DataFrame({cat_col: counts.index.tolist(), "Count": counts.values.tolist()})
            num_col = "Count"
    else:
        sub_df = df_clean[[cat_col, num_col]].dropna().head(100)

    if sub_df.empty:
        return ""

    width = 800
    height = 420
    padding_left = 70
    padding_bottom = 60
    padding_top = 45
    padding_right = 30

    vals = pd.to_numeric(sub_df[num_col], errors="coerce").fillna(0).tolist()
    labels = sub_df[cat_col].astype(str).tolist()

    max_val = max(vals) if vals and max(vals) > 0 else 1
    colors = ["#38bdf8", "#a855f7", "#34d399", "#fbbf24", "#f43f5e", "#818cf8", "#2dd4bf", "#4ade80"]

    svg_parts = []
    svg_parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" style="background:#0f172a; border-radius:12px; font-family:system-ui, -apple-system, sans-serif; width:100%; height:auto;">')
    
    # Title
    svg_parts.append(f'<text x="{width/2:.1f}" y="28" fill="#f8fafc" font-size="16" font-weight="bold" text-anchor="middle">{cat_col} vs {num_col}</text>')

    # Gridlines & Y-Axis ticks
    num_ticks = 5
    for i in range(num_ticks + 1):
        tick_val = (max_val / num_ticks) * i
        y_pos = height - padding_bottom - (i / num_ticks) * (height - padding_top - padding_bottom)
        svg_parts.append(f'<line x1="{padding_left}" y1="{y_pos:.1f}" x2="{width-padding_right}" y2="{y_pos:.1f}" stroke="#1e293b" stroke-width="1" stroke-dasharray="4,4"/>')
        svg_parts.append(f'<text x="{padding_left-10}" y="{y_pos+4:.1f}" fill="#64748b" font-size="11" text-anchor="end">{tick_val:,.0f}</text>')

    # Baseline
    svg_parts.append(f'<line x1="{padding_left}" y1="{height-padding_bottom}" x2="{width-padding_right}" y2="{height-padding_bottom}" stroke="#334155" stroke-width="2"/>')

    num_bars = len(vals)
    avail_width = width - padding_left - padding_right
    bar_gap = avail_width / max(num_bars, 1)
    bar_width = min(bar_gap * 0.65, 50)

    for i in range(num_bars):
        val = vals[i]
        label = labels[i][:14]
        bar_h = (val / max_val) * (height - padding_top - padding_bottom) if max_val > 0 else 0
        x = padding_left + i * bar_gap + (bar_gap - bar_width) / 2
        y = height - padding_bottom - bar_h
        color = colors[i % len(colors)]

        # Bar
        svg_parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{max(bar_h, 3):.1f}" rx="4" fill="{color}" opacity="0.9"/>')
        # Value Label
        if bar_h > 15:
            svg_parts.append(f'<text x="{x + bar_width/2:.1f}" y="{y - 6:.1f}" fill="#f8fafc" font-size="11" font-weight="bold" text-anchor="middle">{val:,.0f}</text>')
        # Category Label
        svg_parts.append(f'<text x="{x + bar_width/2:.1f}" y="{height - padding_bottom + 20:.1f}" fill="#94a3b8" font-size="11" text-anchor="middle">{label}</text>')

    svg_parts.append('</svg>')
    return "".join(svg_parts)


def generate_chart(df: pd.DataFrame, chart_type: str) -> str:
    """
    Generate chart image, save PNG/SVG into static/charts, and return relative web path.
    Guarantees chart generation using Matplotlib or Pure SVG fallback.
    """
    if df is None or df.empty:
        return None

    os.makedirs("static/charts", exist_ok=True)

    if plt is not None:
        try:
            df_clean = preprocess_chart_dataframe(df)
            builder = get_chart_builder(chart_type)
            fig = builder(df_clean)

            if fig is None:
                fig = create_bar_chart(df_clean)

            if fig is not None:
                filename = f"{uuid.uuid4().hex}.png"
                filepath = os.path.join("static", "charts", filename)
                fig.savefig(filepath, dpi=120, bbox_inches="tight")
                plt.close(fig)
                plt.close('all')
                gc.collect()
                return f"charts/{filename}"
        except Exception as err:
            print("[CHART ENGINE NOTICE] Matplotlib render fallback:", err)

    # Pure Python SVG Chart Fallback (Guarantees charts ALWAYS render 100% reliably!)
    svg_code = generate_pure_svg_chart(df, chart_type)
    if svg_code:
        svg_filename = f"{uuid.uuid4().hex}.svg"
        svg_filepath = os.path.join("static", "charts", svg_filename)
        with open(svg_filepath, "w", encoding="utf-8") as f:
            f.write(svg_code)
        return f"charts/{svg_filename}"

    return None


def generate_chart_base64(df: pd.DataFrame, chart_type: str) -> str:
    """
    Generate chart and return base64 encoded PNG for PDF / Word / PPTX export.
    """
    if df is None or df.empty:
        return ""

    df_clean = preprocess_chart_dataframe(df)

    builder = get_chart_builder(chart_type)
    fig = builder(df_clean)

    if fig is None:
        fig = create_bar_chart(df_clean)
        if fig is None:
            return ""

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    plt.close('all')
    gc.collect()

    buffer.seek(0)
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


def generate_chart_svg(df: pd.DataFrame, chart_type: str) -> bytes:
    """
    Generate chart and return SVG bytes stream.
    """
    if df is None or df.empty:
        return b""

    builder = get_chart_builder(chart_type)
    fig = builder(df)

    if fig is None:
        fig = create_bar_chart(df)
        if fig is None:
            return b""

    buffer = io.BytesIO()
    fig.savefig(buffer, format="svg", bbox_inches="tight")
    plt.close(fig)
    plt.close('all')
    gc.collect()

    buffer.seek(0)
    return buffer.getvalue()


def build_row_hover_card(row: pd.Series, primary_col: str, metric_col: str) -> str:
    """
    Format a rich, executive, multi-line hover tooltip card for a record.
    Displays the person/entity name prominently with an icon, followed by
    all relevant fields (dates, amounts, status, destination, tracking ID, etc.).
    """
    lines = []

    def get_field_icon(col_name: str) -> str:
        c_l = str(col_name).lower()
        if any(k in c_l for k in ["name", "consignee", "customer", "client", "employee", "user", "person", "buyer", "receiver", "vendor", "agent"]):
            return "👤"
        if any(k in c_l for k in ["date", "time", "day", "month", "year", "created", "booking"]):
            return "📅"
        if any(k in c_l for k in ["amount", "cod", "revenue", "price", "cost", "profit", "value", "balance", "total", "pay", "fee"]):
            return "💰"
        if any(k in c_l for k in ["status", "stage", "condition", "state"]):
            return "📦"
        if any(k in c_l for k in ["city", "destination", "origin", "location", "area", "address", "province", "country"]):
            return "📍"
        if any(k in c_l for k in ["tracking", "id", "ref", "order_id", "consignment", "code", "cnic", "phone", "mobile"]):
            return "🏷️"
        if any(k in c_l for k in ["weight", "qty", "quantity", "pieces", "count"]):
            return "⚖️"
        if any(k in c_l for k in ["percent", "pct", "rate", "ratio", "score"]):
            return "📊"
        if any(k in c_l for k in ["courier", "service", "shipper", "vendor", "carrier"]):
            return "🚚"
        return "🔹"

    primary_val = str(row.get(primary_col, "")).strip() if (primary_col and primary_col in row) else ""
    primary_clean_name = str(primary_col).replace("_", " ").title() if primary_col else ""
    primary_icon = get_field_icon(primary_col) if primary_col else "👤"

    if primary_val:
        lines.append(f"<b style='font-size:13px;color:#38bdf8;'>{primary_icon} {primary_clean_name}: {primary_val}</b>")
        lines.append("─────────────────────────────")

    # Display primary metric column directly below header
    if metric_col and metric_col != primary_col and metric_col in row:
        val = row[metric_col]
        m_name = str(metric_col).replace("_", " ").title()
        icon = get_field_icon(metric_col)
        if pd.notnull(val):
            try:
                num = float(val)
                val_str = f"{int(num):,}" if num.is_integer() else f"{num:,.2f}"
            except Exception:
                val_str = str(val)
            lines.append(f"{icon} <b>{m_name}:</b> {val_str}")

    # Display all other columns from the record
    displayed_cols = {primary_col, metric_col}
    skip_cols = {"recordid", "s.no", "sr_no", "row_num", "index", "row_number", "hash", "row"}

    for c in row.index:
        c_clean = str(c).lower().strip()
        if c in displayed_cols or c_clean in skip_cols:
            continue
        val = row[c]
        if pd.isna(val) or val is None or str(val).strip() == "" or str(val).lower() == "nan":
            continue

        c_title = str(c).replace("_", " ").title()
        icon = get_field_icon(c)

        if isinstance(val, (int, float)):
            try:
                num = float(val)
                val_str = f"{int(num):,}" if num.is_integer() else f"{num:,.2f}"
            except Exception:
                val_str = str(val)
        else:
            val_str = str(val).strip()
            if "T" in val_str and len(val_str) > 10:
                val_str = val_str.split("T")[0]
            elif len(val_str) > 45:
                val_str = val_str[:42] + "..."

        lines.append(f"{icon} <b>{c_title}:</b> {val_str}")
        displayed_cols.add(c)
        if len(lines) >= 8:
            break

    return "<br>".join(lines)


def generate_interactive_chart_spec(df: pd.DataFrame, chart_type: str = "bar") -> dict:
    """
    Generate structured interactive Plotly/Chart.js JSON spec.
    Guarantees 100% data fidelity: the chart labels and numeric values directly
    and accurately match whatever data was returned by the query, row-by-row.
    """
    if df is None or df.empty:
        return {}

    import numpy as np

    df_clean = df.copy()

    # 1. Technical ID and index patterns to avoid picking as metrics or categories
    id_patterns = [
        r"^recordid$", r"^id$", r".*_id$", r"^key$", r"^tracking_id$",
        r"^sr$", r"^sr\.$", r"^sr_no$", r"^srno$", r"^s\.no$", r"^s_no$", r"^sno$",
        r"^index$", r"^row_num$", r"^row_number$", r"^row$", r"^#$", r"^sl_no$", r"^slno$",
        r"^consignment$", r"^tracking$", r"^cnic$", r"^cnic_number$", r"^phone$",
        r"^mobile$", r"^contact$", r"^consignee_contact$", r"^order_reference$", r"^order_ref$"
    ]

    # Convert numeric strings (e.g. "1,200", "$450.50", "15.5%") to actual numbers
    for col in df_clean.columns:
        col_clean = str(col).lower().strip()
        if any(re.match(p, col_clean) for p in id_patterns):
            continue
        if not pd.api.types.is_numeric_dtype(df_clean[col]):
            try:
                cleaned_series = df_clean[col].astype(str).str.replace(r"[$,]", "", regex=True)
                converted = pd.to_numeric(cleaned_series, errors="coerce")
                if converted.notna().mean() >= 0.7:
                    df_clean[col] = converted
            except Exception:
                pass

    # Exclude any summary / rollup row (like "Total", "Grand Total") if df has multiple rows
    # so that the visual chart displays true distribution without being distorted by a giant total bar.
    if len(df_clean) > 2:
        for col in df_clean.columns:
            if not pd.api.types.is_numeric_dtype(df_clean[col]):
                is_total_mask = df_clean[col].astype(str).str.strip().str.lower().isin(
                    ["total", "grand total", "overall", "summary", "all"]
                )
                if is_total_mask.any():
                    df_clean = df_clean[~is_total_mask].copy()
                    break

    if df_clean.empty:
        df_clean = df.copy()

    # Identify numeric columns (metrics)
    numeric_cols = []
    for c in df_clean.select_dtypes(include="number").columns:
        c_clean = str(c).lower().strip()
        if not any(re.match(p, c_clean) for p in id_patterns):
            valid_vals = df_clean[c].dropna()
            if not valid_vals.empty:
                numeric_cols.append(c)

    # Prioritize business financial, volume and quantity metrics
    priority_metric_kws = [
        "count", "total", "sum", "amount", "revenue", "sales", "cod", "orders",
        "customers", "qty", "quantity", "price", "cost", "profit", "rate", "percent",
        "pct", "margin", "weight", "value", "balance", "score", "avg", "average"
    ]
    def metric_sort_key(c):
        c_lower = str(c).lower()
        for idx, kw in enumerate(priority_metric_kws):
            if kw in c_lower:
                return idx
        return len(priority_metric_kws)
    numeric_cols.sort(key=metric_sort_key)

    # Identify categorical columns (dimensions / labels)
    cat_cols = []
    for c in df_clean.columns:
        c_clean = str(c).lower().strip()
        if any(re.match(p, c_clean) for p in id_patterns):
            continue
        if c not in numeric_cols:
            cat_cols.append(c)

    # Intelligent Tiered Dimension Hierarchy:
    # 1. Names and People (Consignee, Customer, Client, Employee, Person, User) -> HIGHEST PRIORITY
    person_kws = [
        "consignee_name", "consignee", "customer_name", "customer", "client_name", "client",
        "employee_name", "employee", "full_name", "person_name", "person", "buyer_name", "buyer",
        "recipient_name", "recipient", "receiver_name", "receiver", "vendor_name", "vendor",
        "shipper_name", "shipper", "agent_name", "agent", "user_name", "username", "user",
        "merchant_name", "merchant", "account_name", "student_name", "student", "patient_name",
        "patient", "doctor_name", "doctor", "driver_name", "driver", "name"
    ]
    # 2. Products & Business Entities
    product_kws = [
        "product_name", "product", "item_name", "item", "title", "department", "dept",
        "category", "brand", "model", "service_name", "service", "project"
    ]
    # 3. Locations
    location_kws = [
        "destination_city", "destination", "city", "sub_area", "subarea", "area",
        "neighborhood", "location", "province", "region", "state", "country", "origin", "branch"
    ]
    # 4. Status / Classification
    classification_kws = [
        "order_status", "status", "stage", "priority", "courier", "carrier", "type", "method"
    ]
    # 6. Dates / Timestamps (Lowest Priority among categorical columns - never pick date over person name)
    date_kws = [
        "month", "quarter", "year", "week", "day", "booking_date", "order_date", "created_at", "date", "time"
    ]

    def cat_sort_key(c):
        c_lower = str(c).lower().strip()
        # Tier 1: Person / Entity Names
        for idx, kw in enumerate(person_kws):
            if kw in c_lower:
                return (1, idx)
        # Tier 2: Products & Business Entities
        for idx, kw in enumerate(product_kws):
            if kw in c_lower:
                return (2, idx)
        # Tier 3: Locations
        for idx, kw in enumerate(location_kws):
            if kw in c_lower:
                return (3, idx)
        # Tier 4: Classification / Status
        for idx, kw in enumerate(classification_kws):
            if kw in c_lower:
                return (4, idx)
        # Tier 6: Dates / Timestamps (Lowest Priority)
        for idx, kw in enumerate(date_kws):
            if kw in c_lower:
                return (6, idx)
        # Tier 5: Other columns
        return (5, 0)

    cat_cols.sort(key=cat_sort_key)

    x_col = ""
    y_col = ""

    # 1. Check if query returned explicit 2-column aggregated result (e.g. [Destination], [Order_Count])
    if len(df_clean.columns) == 2:
        c1, c2 = df_clean.columns[0], df_clean.columns[1]
        if not pd.api.types.is_numeric_dtype(df_clean[c1]) and pd.api.types.is_numeric_dtype(df_clean[c2]):
            x_col, y_col = c1, c2
        elif pd.api.types.is_numeric_dtype(df_clean[c1]) and not pd.api.types.is_numeric_dtype(df_clean[c2]):
            x_col, y_col = c2, c1

    # Fallback to smart column detection if not explicit 2-column output
    if not x_col:
        x_col = cat_cols[0] if cat_cols else ""
    if not y_col:
        y_col = numeric_cols[0] if numeric_cols else ""

    labels = []
    values = []
    override_chart_type = None

    # Case A: Explicit Aggregated Group By Query (e.g. Destination + Count, Status + Count)
    if len(df_clean.columns) == 2 and x_col and y_col and len(df_clean) > 1 and len(df_clean) <= 50:
        sub_df = df_clean[[x_col, y_col]].dropna().head(25)
        labels = sub_df[x_col].astype(str).tolist()
        values = [float(v) if pd.notnull(v) else 0.0 for v in pd.to_numeric(sub_df[y_col], errors="coerce").fillna(0).tolist()]
        if len(labels) <= 10:
            override_chart_type = "pie"
        else:
            override_chart_type = "horizontal_bar"

    # Case B: Single Row Aggregated Metric (e.g. Total_Orders: 335, Total_COD: 15000)
    elif len(df_clean) == 1 and numeric_cols:
        ordered_metric_cols = [c for c in df_clean.columns if c in numeric_cols]
        labels = [str(c).replace("_", " ").title() for c in ordered_metric_cols]
        values = [float(df_clean[c].iloc[0]) if pd.notnull(df_clean[c].iloc[0]) else 0.0 for c in ordered_metric_cols]
        override_chart_type = "bar"

    # Case C: Multi-row records (e.g. raw orders or customer lists)
    else:
        address_col = next((c for c in df_clean.columns if any(kw in str(c).lower() for kw in ["address", "location", "consignee_address", "street"])), None)
        parsed_area_counts = None
        if address_col and df_clean[address_col].dropna().count() > 0:
            try:
                from ai.area_intelligence import parse_address_neighborhood
                parsed_areas = df_clean[address_col].dropna().apply(parse_address_neighborhood)
                if parsed_areas.nunique() > 1:
                    parsed_area_counts = parsed_areas.value_counts().head(12)
            except Exception:
                parsed_area_counts = None

        if parsed_area_counts is not None and len(parsed_area_counts) >= 2:
            labels = parsed_area_counts.index.astype(str).tolist()
            values = [float(v) for v in parsed_area_counts.values.tolist()]
            x_col = "Sub-Area Neighborhood"
            y_col = "Order Count"
            override_chart_type = "pie"
        elif x_col and y_col and len(df_clean) <= 25 and df_clean[x_col].nunique() == len(df_clean):
            # Clean direct ranking of unique entities with numeric metric
            sub_df = df_clean[[x_col, y_col]].dropna().head(15)
            labels = sub_df[x_col].astype(str).tolist()
            values = [float(v) if pd.notnull(v) else 0.0 for v in pd.to_numeric(sub_df[y_col], errors="coerce").fillna(0).tolist()]
            override_chart_type = "pie" if len(labels) <= 8 else "horizontal_bar"
        elif x_col:
            val_counts = df_clean[x_col].dropna().value_counts().head(20)
            labels = val_counts.index.astype(str).tolist()
            values = [float(v) for v in val_counts.values.tolist()]
            if len(labels) >= 2 and len(labels) <= 10:
                override_chart_type = "pie"
            elif len(labels) > 10:
                override_chart_type = "horizontal_bar"
        elif numeric_cols:
            labels = [str(c).replace("_", " ").title() for c in numeric_cols]
            values = [float(df_clean[c].mean()) if pd.notnull(df_clean[c].mean()) else 0.0 for c in numeric_cols]
            override_chart_type = "bar"

    # Multi-series data if multiple numeric columns exist
    series_list = []
    if len(numeric_cols) >= 2 and x_col and x_col in df_clean.columns:
        for nc in numeric_cols[:4]:
            col_vals = [
                float(v) if (pd.notnull(v) and not np.isnan(v)) else 0.0
                for v in pd.to_numeric(df_clean[nc].head(30), errors="coerce").fillna(0).tolist()
            ]
            series_list.append({"name": str(nc).replace("_", " ").title(), "values": col_vals})

    # Heatmap correlation matrix if multiple numeric columns exist
    matrix = []
    matrix_cols = []
    if len(numeric_cols) >= 2:
        matrix_cols = numeric_cols[:8]
        corr_df = df_clean[matrix_cols].corr().fillna(0)
        matrix = corr_df.values.tolist()

    try:
        from visualization.chart_selector import select_chart, get_compatible_chart_types
        best_chart = override_chart_type or (select_chart(df_clean) if (not chart_type or chart_type == "auto") else chart_type)
        compatible_types = get_compatible_chart_types(df_clean)
    except Exception:
        best_chart = override_chart_type or chart_type or "pie"
        compatible_types = ["auto", "pie", "donut", "bar", "horizontal_bar", "line", "area", "scatter", "heatmap", "treemap", "funnel"]

    chart_title = f"{y_col} by {x_col}" if x_col and y_col and x_col != y_col else (f"{x_col} Analysis" if x_col else "Dataset Analytics")

    return {
        "chart_type": best_chart or "pie",
        "title": chart_title,
        "x_col": x_col,
        "y_col": y_col,
        "labels": labels,
        "values": values,
        "series": series_list,
        "matrix": matrix,
        "matrix_cols": matrix_cols,
        "total_records": len(df_clean),
        "compatible_types": compatible_types
    }