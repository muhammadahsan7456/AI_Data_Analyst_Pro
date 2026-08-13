import io
import re
import gc
import base64
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

# Professional Dark/Light color palette
# Professional Modern Enterprise color palette
COLOR_PALETTE = [
    "#38bdf8", "#a855f7", "#34d399", "#fbbf24",
    "#f43f5e", "#818cf8", "#2dd4bf", "#4ade80",
    "#c084fc", "#f472b6", "#fb923c", "#60a5fa"
]

MAX_CHART_POINTS = 5000


def setup_style(dark: bool = True):
    plt.close('all')
    fig_bg = "#0f172a" if dark else "#ffffff"
    ax_bg = "#1e293b" if dark else "#f8fafc"
    text_color = "#f8fafc" if dark else "#0f172a"
    grid_color = "#334155" if dark else "#e2e8f0"

    plt.rcParams.update({
        'font.family': 'sans-serif',
        'font.size': 11,
        'figure.facecolor': fig_bg,
        'axes.facecolor': ax_bg,
        'axes.edgecolor': grid_color,
        'axes.labelcolor': text_color,
        'axes.titlesize': 14,
        'axes.titlecolor': text_color,
        'axes.titleweight': 'bold',
        'axes.labelsize': 11,
        'axes.labelweight': 'bold',
        'xtick.color': text_color,
        'ytick.color': text_color,
        'grid.color': grid_color,
        'grid.linestyle': '--',
        'grid.alpha': 0.5,
        'figure.autolayout': True
    })


def downsample_dataframe(df: pd.DataFrame, max_rows: int = MAX_CHART_POINTS) -> pd.DataFrame:
    if len(df) <= max_rows:
        return df
    step = int(np.ceil(len(df) / max_rows))
    return df.iloc[::step]


def get_clean_categorical_columns(df: pd.DataFrame) -> list:
    id_patterns = [
        r"^recordid$", r"^id$", r"^uuid$", r"^hash$", r"^index$", r"^primarykey$",
        r".*_id$", r".*id$", r"^key$"
    ]
    
    clean_cols = []
    for col in df.columns:
        col_lower = str(col).lower().strip()
        
        if any(re.match(pattern, col_lower) for pattern in id_patterns):
            continue
            
        if not pd.api.types.is_numeric_dtype(df[col]):
            clean_cols.append(col)
        elif df[col].nunique() <= 20 and df[col].nunique() > 1:
            clean_cols.append(col)

    if not clean_cols:
        exclude_numeric = df.select_dtypes(exclude="number").columns.tolist()
        if exclude_numeric:
            clean_cols = exclude_numeric

    return clean_cols

def preprocess_chart_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure string numbers (e.g. '2200.0', '$150.00') and date strings (e.g. '2025-07-26')
    are converted to true numeric and datetime dtypes for professional Matplotlib plotting.
    Excludes technical record IDs from being converted to numeric metrics.
    """
    if df is None or df.empty:
        return df

    df_clean = df.copy()

    for col in df_clean.columns:
        col_lower = str(col).lower().strip()
        if col_lower in ["recordid", "id", "hash", "row_number"] or col_lower.endswith("_id") or col_lower.startswith("order_id"):
            df_clean[col] = df_clean[col].astype(str)
            continue

        if not pd.api.types.is_numeric_dtype(df_clean[col]):
            cleaned_s = df_clean[col].astype(str).str.replace(r"[$,]", "", regex=True)
            converted = pd.to_numeric(cleaned_s, errors="coerce")
            if converted.notna().mean() > 0.5:
                df_clean[col] = converted

        if df_clean[col].dtype in ['object', 'string']:
            sample = df_clean[col].dropna().head(20).astype(str)
            if sample.str.contains(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$|^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$", regex=True).mean() > 0.5:
                try:
                    df_clean[col] = pd.to_datetime(df_clean[col], errors="coerce")
                except Exception:
                    pass

    return df_clean


def get_numeric_columns_clean(df: pd.DataFrame) -> list:
    id_patterns = [r"^recordid$", r"^id$", r".*_id$", r"^key$", r"^tracking_id$"]
    numeric_cols = []
    for col in df.select_dtypes(include="number").columns:
        if not any(re.match(p, str(col).lower().strip()) for p in id_patterns):
            numeric_cols.append(col)
    return numeric_cols


# 1. BAR CHART
def create_bar_chart(dataframe: pd.DataFrame):
    setup_style(dark=True)
    df = preprocess_chart_dataframe(dataframe)
    numeric = get_numeric_columns_clean(df)
    categorical = get_clean_categorical_columns(df)

    total_records = len(df)

    if not numeric or not categorical:
        if categorical:
            x = categorical[0]
            val_counts = df[x].value_counts()
            data = val_counts.head(12)
            fig, ax = plt.subplots(figsize=(10, 5))
            bars = ax.bar(data.index.astype(str), data.values, color=COLOR_PALETTE[0], edgecolor="none", width=0.6, zorder=3)
            ax.grid(True, axis='y')
            ax.set_title(f"{x} Distribution (Total {total_records} Records)", pad=15)
            ax.set_xlabel(x)
            ax.set_ylabel("Count")
            for bar in bars:
                h = bar.get_height()
                ax.annotate(f"{h:,.0f}", xy=(bar.get_x() + bar.get_width() / 2, h),
                            xytext=(0, 4), textcoords="offset points", ha='center', va='bottom', color='#f8fafc', fontweight='bold')
            plt.xticks(rotation=30, ha="right")
            plt.tight_layout()
            return fig
        return None

    cat0 = categorical[0]
    num0 = numeric[0]
    x_col = cat0[0] if isinstance(cat0, (list, tuple)) else cat0
    y_col = num0[0] if isinstance(num0, (list, tuple)) else num0
    sample_df = downsample_dataframe(df[[x_col, y_col]].dropna(), max_rows=10000)

    # If categorical column has high cardinality (e.g. unique Order IDs per record like 95 unique Order IDs),
    # plot overall record sequence trend so all 95 records are included on chart!
    unique_cnt = sample_df[x_col].nunique()
    if isinstance(unique_cnt, pd.Series):
        unique_cnt = int(unique_cnt.iloc[0])
    else:
        unique_cnt = int(unique_cnt)

    if unique_cnt > 15:
        data = np.array(sample_df[y_col].values, dtype=float).flatten()
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(range(1, len(data) + 1), data, marker="o", color=COLOR_PALETTE[0], linewidth=2, markersize=3 if len(data) > 100 else 5, zorder=3)
        ax.fill_between(range(1, len(data) + 1), data, color=COLOR_PALETTE[0], alpha=0.15)
        ax.grid(True)
        ax.set_title(f"{y_col} Record Sequence Analysis (Total {total_records} Query Records)", pad=15)
        ax.set_xlabel(f"Record Index (1 to {len(data)})")
        ax.set_ylabel(y_col)
        plt.tight_layout()
        return fig

    data = sample_df.groupby(x_col)[y_col].sum().sort_values(ascending=False).head(12)

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(data.index.astype(str), data.values, color=COLOR_PALETTE[0], edgecolor="none", width=0.6, zorder=3)
    ax.grid(True, axis='y')
    ax.set_title(f"{y_col} by {x_col} (Total {total_records} Query Records)", pad=15)
    ax.set_xlabel(x_col)
    ax.set_ylabel(y_col)
    for bar in bars:
        h = bar.get_height()
        ax.annotate(f"{h:,.0f}" if h >= 10 else f"{h:,.1f}", xy=(bar.get_x() + bar.get_width() / 2, h),
                    xytext=(0, 4), textcoords="offset points", ha='center', va='bottom', color='#f8fafc', fontweight='bold')
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    return fig


# 2. LINE CHART
def create_line_chart(dataframe: pd.DataFrame):
    setup_style(dark=True)
    df = preprocess_chart_dataframe(dataframe)
    numeric = get_numeric_columns_clean(df)
    if not numeric:
        return None

    y = numeric[0]
    total_records = len(df)
    data = downsample_dataframe(df[[y]].dropna(), max_rows=5000)

    y_vals = np.array(data[y].values, dtype=float).flatten()
    fig, ax = plt.subplots(figsize=(10, 5))
    x_range = range(1, len(y_vals) + 1)
    ax.plot(x_range, y_vals, marker="o", color=COLOR_PALETTE[1], linewidth=2.5, markersize=3 if len(y_vals) > 100 else 5, zorder=3)
    ax.fill_between(x_range, y_vals, color=COLOR_PALETTE[1], alpha=0.15)
    ax.grid(True)
    ax.set_title(f"{y} Complete Trend Analysis (Total {total_records} Query Records)", pad=15)
    ax.set_xlabel(f"Record Index (1 to {len(data)})")
    ax.set_ylabel(y)
    plt.tight_layout()
    return fig


# 3. PIE CHART
def create_pie_chart(dataframe: pd.DataFrame):
    setup_style()
    df = preprocess_chart_dataframe(dataframe)
    numeric = get_numeric_columns_clean(df)
    categorical = get_clean_categorical_columns(df)

    if not numeric or not categorical:
        if categorical:
            data = df[categorical[0]].value_counts().head(6)
            if data.empty:
                return None
            fig, ax = plt.subplots(figsize=(7, 7))
            wedges, texts, autotexts = ax.pie(
                data.values,
                labels=data.index.astype(str),
                autopct="%1.1f%%",
                startangle=140,
                colors=COLOR_PALETTE[:len(data)]
            )
            plt.setp(autotexts, size=10, weight="bold", color="white")
            ax.set_title(f"{categorical[0]} Distribution", fontweight="bold", pad=15)
            plt.tight_layout()
            return fig
        return None

    x, y = categorical[0], numeric[0]
    sample_df = downsample_dataframe(df[[x, y]].dropna(), max_rows=10000)
    data = sample_df.groupby(x)[y].sum().sort_values(ascending=False).head(6)

    fig, ax = plt.subplots(figsize=(7, 7))
    wedges, texts, autotexts = ax.pie(
        data.values,
        labels=data.index.astype(str),
        autopct="%1.1f%%",
        startangle=140,
        colors=COLOR_PALETTE[:len(data)]
    )
    plt.setp(autotexts, size=10, weight="bold", color="white")
    ax.set_title(f"{y} Distribution by {x}", fontweight="bold", pad=15)
    plt.tight_layout()
    return fig


# 4. HISTOGRAM
def create_histogram(dataframe: pd.DataFrame):
    setup_style()
    df = preprocess_chart_dataframe(dataframe)
    numeric = get_numeric_columns_clean(df)
    if not numeric:
        return None

    y = numeric[0]
    data = downsample_dataframe(df[[y]].dropna(), max_rows=10000)[y]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(data, bins=20, color=COLOR_PALETTE[2], edgecolor="white")
    ax.set_title(f"{y} Frequency Distribution", fontweight="bold", pad=15)
    ax.set_xlabel(y)
    ax.set_ylabel("Frequency")
    plt.tight_layout()
    return fig


# 5. SCATTER CHART
def create_scatter_chart(dataframe: pd.DataFrame):
    setup_style()
    df = preprocess_chart_dataframe(dataframe)
    numeric = get_numeric_columns_clean(df)
    if len(numeric) < 2:
        return None

    x, y = numeric[0], numeric[1]
    data = downsample_dataframe(df[[x, y]].dropna(), max_rows=3000)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.scatter(data[x], data[y], color=COLOR_PALETTE[3], alpha=0.6, s=15)
    ax.set_title(f"Correlation: {y} vs {x}", fontweight="bold", pad=15)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    plt.tight_layout()
    return fig


# 6. BOXPLOT
def create_box_plot(dataframe: pd.DataFrame):
    setup_style()
    df = preprocess_chart_dataframe(dataframe)
    numeric = get_numeric_columns_clean(df)
    if not numeric:
        return None

    cols_to_plot = numeric[:4]
    data = downsample_dataframe(df[cols_to_plot].dropna(), max_rows=5000)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.boxplot([data[col] for col in cols_to_plot], labels=cols_to_plot, patch_artist=True)
    ax.set_title("Numeric Outliers & Quartiles", fontweight="bold", pad=15)
    plt.tight_layout()
    return fig


# 7. AREA CHART
def create_area_chart(df: pd.DataFrame):
    setup_style()
    numeric = df.select_dtypes(include="number").columns.tolist()
    if not numeric:
        return None

    y = numeric[0]
    data = downsample_dataframe(df[[y]].dropna(), max_rows=1000)[y]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.fill_between(range(len(data)), data.values, color=COLOR_PALETTE[4], alpha=0.4)
    ax.plot(data.values, color=COLOR_PALETTE[4], linewidth=2)
    ax.set_title(f"{y} Cumulative Area Analysis", fontweight="bold", pad=15)
    ax.set_xlabel("Sample Index")
    ax.set_ylabel(y)
    plt.tight_layout()
    return fig


# 8. HEATMAP
def create_heatmap(df: pd.DataFrame):
    setup_style()
    numeric_df = df.select_dtypes(include="number")
    if numeric_df.shape[1] < 2:
        return None

    sample_numeric = downsample_dataframe(numeric_df, max_rows=10000)
    corr = sample_numeric.corr().round(2)

    fig, ax = plt.subplots(figsize=(8, 6))
    cax = ax.matshow(corr, cmap="coolwarm", vmin=-1, vmax=1)
    fig.colorbar(cax)

    ticks = np.arange(0, len(corr.columns), 1)
    ax.set_xticks(ticks)
    ax.set_yticks(ticks)
    ax.set_xticklabels(corr.columns, rotation=45, ha="left")
    ax.set_yticklabels(corr.columns)

    for i in range(len(corr.columns)):
        for j in range(len(corr.columns)):
            ax.text(j, i, str(corr.iloc[i, j]), ha="center", va="center", color="black", fontsize=9, fontweight="bold")

    ax.set_title("Correlation Heatmap Matrix", fontweight="bold", pad=25)
    plt.tight_layout()
    return fig


# 9. RADAR CHART
def create_radar_chart(df: pd.DataFrame):
    setup_style()
    numeric = df.select_dtypes(include="number").columns.tolist()
    if len(numeric) < 3:
        return None

    cols = numeric[:6]
    means = df[cols].mean()

    # Normalize values between 0 and 100 for comparison
    max_vals = df[cols].max().replace(0, 1)
    normalized = (means / max_vals * 100).values.tolist()
    normalized += normalized[:1]

    angles = [n / float(len(cols)) * 2 * np.pi for n in range(len(cols))]
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(7, 7), subplot_kw=dict(polar=True))
    ax.plot(angles, normalized, color=COLOR_PALETTE[5], linewidth=2)
    ax.fill(angles, normalized, color=COLOR_PALETTE[5], alpha=0.25)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(cols)
    ax.set_title("Multi-Metric Radar Comparison", fontweight="bold", pad=20)
    plt.tight_layout()
    return fig


# 10. TREEMAP CHART
def create_treemap_chart(df: pd.DataFrame):
    setup_style()
    numeric = df.select_dtypes(include="number").columns.tolist()
    categorical = get_clean_categorical_columns(df)

    if not numeric or not categorical:
        return None

    x, y = categorical[0], numeric[0]
    data = df.groupby(x)[y].sum().sort_values(ascending=False).head(8)

    fig, ax = plt.subplots(figsize=(10, 5))
    sizes = data.values
    labels = [f"{idx}\n{val:,.0f}" for idx, val in zip(data.index, sizes)]
    
    # Custom Treemap Layout using Rectangles
    norm_sizes = sizes / sizes.sum()
    x_offset = 0
    colors = COLOR_PALETTE[:len(sizes)]
    
    for i, (norm, label) in enumerate(zip(norm_sizes, labels)):
        width = norm
        ax.barh(0, width, left=x_offset, color=colors[i % len(colors)], edgecolor="white")
        ax.text(x_offset + width / 2, 0, label, ha="center", va="center", color="white", fontweight="bold", fontsize=9)
        x_offset += width

    ax.set_axis_off()
    ax.set_title(f"Treemap: {y} breakdown by {x}", fontweight="bold", pad=15)
    plt.tight_layout()
    return fig


# 11. SUNBURST CHART
def create_sunburst_chart(df: pd.DataFrame):
    setup_style()
    numeric = df.select_dtypes(include="number").columns.tolist()
    categorical = get_clean_categorical_columns(df)

    if not numeric or not categorical:
        return None

    x, y = categorical[0], numeric[0]
    data = df.groupby(x)[y].sum().sort_values(ascending=False).head(8)

    fig, ax = plt.subplots(figsize=(7, 7))
    vals = data.values
    names = data.index.astype(str)

    # Concentric Ring Chart
    size = 0.3
    ax.pie(vals, radius=1, colors=COLOR_PALETTE[:len(vals)], labels=names,
           wedgeprops=dict(width=size, edgecolor='white'))
    ax.set_title(f"Sunburst Hierarchy: {y} by {x}", fontweight="bold", pad=15)
    plt.tight_layout()
    return fig


# 12. WATERFALL CHART
def create_waterfall_chart(df: pd.DataFrame):
    setup_style()
    numeric = df.select_dtypes(include="number").columns.tolist()
    categorical = get_clean_categorical_columns(df)

    if not numeric:
        return None

    if categorical:
        x, y = categorical[0], numeric[0]
        data = df.groupby(x)[y].mean().head(6)
    else:
        y = numeric[0]
        data = df[y].head(6)

    fig, ax = plt.subplots(figsize=(10, 5))
    values = data.values
    index = data.index.astype(str) if hasattr(data, 'index') else [f"Step {i+1}" for i in range(len(values))]
    
    cumulative = np.cumsum(values)
    starts = np.pad(cumulative[:-1], (1, 0), 'constant')

    ax.bar(index, values, bottom=starts, color=COLOR_PALETTE[6], edgecolor="white")
    ax.plot(index, cumulative, color="black", marker="o")
    ax.set_title(f"Waterfall Variance: {y}", fontweight="bold", pad=15)
    plt.xticks(rotation=30)
    plt.tight_layout()
    return fig


# 13. FUNNEL CHART
def create_funnel_chart(df: pd.DataFrame):
    setup_style()
    numeric = df.select_dtypes(include="number").columns.tolist()
    categorical = get_clean_categorical_columns(df)

    if not numeric or not categorical:
        return None

    x, y = categorical[0], numeric[0]
    data = df.groupby(x)[y].sum().sort_values(ascending=False).head(5)

    fig, ax = plt.subplots(figsize=(8, 5))
    y_pos = np.arange(len(data))
    widths = data.values
    max_w = widths.max() if len(widths) > 0 else 1

    for i, (w, label) in enumerate(zip(widths, data.index.astype(str))):
        left_offset = (max_w - w) / 2
        ax.barh(i, w, left=left_offset, color=COLOR_PALETTE[i % len(COLOR_PALETTE)], height=0.6)
        ax.text(max_w / 2, i, f"{label}: {w:,.0f}", ha="center", va="center", color="white", fontweight="bold")

    ax.invert_yaxis()
    ax.set_axis_off()
    ax.set_title(f"Funnel Conversion: {y} by {x}", fontweight="bold", pad=15)
    plt.tight_layout()
    return fig


# 14. BUBBLE CHART
def create_bubble_chart(df: pd.DataFrame):
    setup_style()
    numeric = df.select_dtypes(include="number").columns.tolist()
    if len(numeric) < 3:
        return create_scatter_chart(df)

    x, y, z = numeric[0], numeric[1], numeric[2]
    data = downsample_dataframe(df[[x, y, z]].dropna(), max_rows=1000)

    # Normalize bubble sizes
    z_norm = (data[z] - data[z].min()) / (data[z].max() - data[z].min() + 1e-6) * 300 + 20

    fig, ax = plt.subplots(figsize=(10, 5))
    sc = ax.scatter(data[x], data[y], s=z_norm, color=COLOR_PALETTE[7], alpha=0.5, edgecolors="black")
    ax.set_title(f"Bubble Analysis: {y} vs {x} (Size: {z})", fontweight="bold", pad=15)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    plt.tight_layout()
    return fig


# 15. CANDLESTICK CHART
def create_candlestick_chart(df: pd.DataFrame):
    setup_style()
    numeric = df.select_dtypes(include="number").columns.tolist()
    if not numeric:
        return None

    y = numeric[0]
    data = downsample_dataframe(df[[y]].dropna(), max_rows=50)[y].values

    if len(data) < 4:
        return create_line_chart(df)

    opens = data[:-1]
    closes = data[1:]
    highs = np.maximum(opens, closes) + np.abs(np.random.normal(0, 1, len(opens)))
    lows = np.minimum(opens, closes) - np.abs(np.random.normal(0, 1, len(opens)))

    fig, ax = plt.subplots(figsize=(10, 5))
    for i in range(len(opens)):
        color = "#10b981" if closes[i] >= opens[i] else "#ef4444"
        ax.plot([i, i], [lows[i], highs[i]], color=color, linewidth=1)
        ax.bar(i, abs(closes[i] - opens[i]), bottom=min(opens[i], closes[i]), color=color, width=0.6)

    ax.set_title(f"Candlestick Price Volatility: {y}", fontweight="bold", pad=15)
    ax.set_xlabel("Time Interval")
    ax.set_ylabel(y)
    plt.tight_layout()
    return fig


def show_chart(chart_type: str, dataframe: pd.DataFrame):
    chart_type = chart_type.lower().strip()
    chart_map = {
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

    if chart_type in chart_map:
        fig = chart_map[chart_type](dataframe)
        if fig:
            plt.show()
            plt.close(fig)
            gc.collect()
    else:
        print(f"❌ Unsupported Chart Type: {chart_type}")