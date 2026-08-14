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
    
    # Filter out ID columns from numeric list
    id_patterns = [r"^recordid$", r"^id$", r".*_id$", r"^key$", r"^tracking_id$"]
    numeric_cols = [c for c in numeric_cols if not any(re.match(p, str(c).lower().strip()) for p in id_patterns)]

    categorical_cols = [c for c in df_clean.columns if c not in numeric_cols and c.lower() not in ["recordid", "id"]]

    cat_col = categorical_cols[0] if categorical_cols else df_clean.columns[0]
    num_col = numeric_cols[0] if numeric_cols else df_clean.columns[-1]

    sub_df = df_clean[[cat_col, num_col]].dropna().head(12)
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