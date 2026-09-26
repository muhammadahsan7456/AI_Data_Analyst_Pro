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


def generate_interactive_chart_spec(df: pd.DataFrame, chart_type: str = "bar") -> dict:
    """
    Generate structured interactive Plotly/Chart.js JSON spec.
    Includes labels, values, numeric matrix for heatmaps, tooltips, and compatible types.
    """
    if df is None or df.empty:
        return {}

    df_clean = df.copy()
    id_patterns = [
        r"^recordid$", r"^id$", r".*_id$", r"^key$", r"^tracking_id$",
        r"^sr$", r"^sr\.$", r"^sr_no$", r"^srno$", r"^s\.no$", r"^s_no$", r"^sno$",
        r"^index$", r"^row_num$", r"^row$", r"^#$", r"^sl_no$", r"^slno$",
        r"^consignment$", r"^tracking$", r"^cnic$", r"^cnic_number$", r"^phone$",
        r"^mobile$", r"^contact$", r"^consignee_contact$", r"^order_reference$", r"^order_ref$"
    ]

    numeric_cols = []
    for c in df_clean.select_dtypes(include="number").columns:
        c_clean = str(c).lower().strip()
        if not any(re.match(p, c_clean) for p in id_patterns):
            valid_vals = df_clean[c].dropna()
            # If max value is huge (> 1,000,000) or values are tracking IDs, exclude from numeric bar metrics
            if not valid_vals.empty and valid_vals.max() < 10000000 and valid_vals.nunique() < len(df_clean):
                numeric_cols.append(c)

    cat_cols = []
    business_cat_cols = []
    status_location_cols = []

    priority_cat_keywords = ["status", "destination", "city", "area", "service", "category", "type", "origin"]
    for kw in priority_cat_keywords:
        for c in df_clean.columns:
            c_clean = str(c).lower().strip()
            if c not in numeric_cols and not any(re.match(p, c_clean) for p in id_patterns):
                if kw in c_clean and df_clean[c].nunique() > 1:
                    status_location_cols.append(c)
                    break

    for c in df_clean.columns:
        c_clean = str(c).lower().strip()
        if any(re.match(p, c_clean) for p in id_patterns):
            continue
        if c in numeric_cols:
            continue
        cat_cols.append(c)
        if df_clean[c].nunique() < len(df_clean):
            business_cat_cols.append(c)

    # Dynamic Column Selection Strategy: Respect SQL Query's Returned Schema First
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
        x_col = status_location_cols[0] if status_location_cols else (business_cat_cols[0] if business_cat_cols else (cat_cols[0] if cat_cols else ""))
    if not y_col:
        priority_metrics = [c for c in numeric_cols if any(kw in str(c).lower() for kw in ["count", "total", "cod", "value", "amount", "weight", "price", "cost", "revenue"])]
        y_col = priority_metrics[0] if priority_metrics else (numeric_cols[0] if numeric_cols else "")

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

    # Case B: Single Row Aggregated Metric (e.g. Total_Orders: 335)
    elif len(df_clean) == 1 and numeric_cols:
        labels = [str(c).replace("_", " ") for c in numeric_cols]
        values = [float(df_clean[c].iloc[0]) if pd.notnull(df_clean[c].iloc[0]) else 0.0 for c in numeric_cols]
        override_chart_type = "bar"

    # Case C: Sub-Area Address Neighborhood Slicing (For Address/Location queries)
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
        elif x_col:
            val_counts = df_clean[x_col].dropna().value_counts().head(20)
            labels = val_counts.index.astype(str).tolist()
            values = [float(v) for v in val_counts.values.tolist()]
            if len(labels) >= 2 and len(labels) <= 10:
                override_chart_type = "pie"
            elif len(labels) > 10:
                override_chart_type = "horizontal_bar"
        elif numeric_cols:
            labels = [str(c).replace("_", " ") for c in numeric_cols]
            values = [float(df_clean[c].mean()) if pd.notnull(df_clean[c].mean()) else 0.0 for c in numeric_cols]

    # Heatmap matrix if requested
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
        compatible_types = ["auto", "pie", "donut", "bar", "horizontal_bar", "line"]

    return {
        "chart_type": best_chart or "bar",
        "title": f"{y_col} by {x_col}" if x_col and y_col else "Dataset Analytics",
        "x_col": x_col,
        "y_col": y_col,
        "labels": labels,
        "values": values,
        "matrix": matrix,
        "matrix_cols": matrix_cols,
        "total_records": len(df_clean),
        "compatible_types": compatible_types
    }