import os
import sys
import io
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


def generate_chart(df: pd.DataFrame, chart_type: str) -> str:
    """
    Generate chart image, save PNG into static/charts, and return relative web path.
    """
    if df is None or df.empty:
        return None

    df_clean = preprocess_chart_dataframe(df)

    builder = get_chart_builder(chart_type)
    fig = builder(df_clean)

    if fig is None:
        fig = create_bar_chart(df_clean)
        if fig is None:
            return None

    os.makedirs("static/charts", exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    filepath = os.path.join("static", "charts", filename)

    fig.savefig(filepath, dpi=120, bbox_inches="tight")
    plt.close(fig)
    plt.close('all')
    gc.collect()

    return f"charts/{filename}"


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