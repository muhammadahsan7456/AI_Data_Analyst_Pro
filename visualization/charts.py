import io
import re
import gc
import base64
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as path_effects
except Exception:
    matplotlib = None
    plt = None
    path_effects = None
import pandas as pd
import numpy as np

# Professional Modern SaaS Color Palette
COLOR_PALETTE = [
    "#38bdf8", "#a855f7", "#34d399", "#fbbf24",
    "#f43f5e", "#818cf8", "#2dd4bf", "#4ade80",
    "#c084fc", "#f472b6", "#fb923c", "#60a5fa"
]

MAX_CHART_POINTS = 5000


def setup_style(dark: bool = True):
    if not plt:
        return
    plt.close('all')
    fig_bg = "#0b0f19" if dark else "#ffffff"
    ax_bg = "#111827" if dark else "#f8fafc"
    text_color = "#f3f4f6" if dark else "#0f172a"
    grid_color = "#1f2937" if dark else "#e2e8f0"

    plt.rcParams.update({
        'font.family': 'sans-serif',
        'font.sans-serif': ['Inter', 'Roboto', 'Arial', 'DejaVu Sans'],
        'font.size': 11,
        'figure.facecolor': fig_bg,
        'axes.facecolor': ax_bg,
        'axes.edgecolor': grid_color,
        'axes.linewidth': 1.2,
        'axes.labelcolor': text_color,
        'axes.titlesize': 15,
        'axes.titlecolor': '#38bdf8' if dark else '#0284c7',
        'axes.titleweight': 'bold',
        'axes.labelsize': 11,
        'axes.labelweight': 'bold',
        'xtick.color': text_color,
        'ytick.color': text_color,
        'grid.color': grid_color,
        'grid.linestyle': '--',
        'grid.alpha': 0.6,
        'figure.autolayout': True,
        'figure.dpi': 150,
        'savefig.dpi': 200
    })


def downsample_dataframe(df: pd.DataFrame, max_rows: int = MAX_CHART_POINTS) -> pd.DataFrame:
    if len(df) <= max_rows:
        return df
    step = int(np.ceil(len(df) / max_rows))
    return df.iloc[::step]


def get_clean_categorical_columns(df: pd.DataFrame) -> list:
    id_patterns = [
        r"^recordid$", r"^id$", r"^uuid$", r"^hash$", r"^index$", r"^primarykey$",
        r".*_id$", r".*id$", r"^key$", r"^sr$", r"^s\.no$", r"^s_no$", r"^sno$", r"^row_num$"
    ]
    
    clean_cols = []
    constant_cols = []
    for col in df.columns:
        col_lower = str(col).lower().strip()
        
        if any(re.match(pattern, col_lower) for pattern in id_patterns):
            continue
            
        if not pd.api.types.is_numeric_dtype(df[col]):
            if df[col].nunique() > 1:
                clean_cols.append(col)
            else:
                constant_cols.append(col)
        elif df[col].nunique() <= 20 and df[col].nunique() > 1:
            clean_cols.append(col)

    if not clean_cols:
        clean_cols = constant_cols if constant_cols else df.select_dtypes(exclude="number").columns.tolist()

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


# 1. ENHANCED BAR CHART WITH MULTI-COLOR GRADIENT & BADGES
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
            data = val_counts.head(10)
            fig, ax = plt.subplots(figsize=(10, 5.5))
            colors = COLOR_PALETTE[:len(data)]
            bars = ax.bar(data.index.astype(str), data.values, color=colors, edgecolor="none", width=0.55, zorder=3, alpha=0.9)
            ax.grid(True, axis='y', linestyle='--', alpha=0.35)
            ax.set_title(f"{x} Distribution Analysis ({total_records:,} Records)", pad=18)
            ax.set_xlabel(x, labelpad=10)
            ax.set_ylabel("Record Count", labelpad=10)
            
            for bar in bars:
                h = bar.get_height()
                ax.annotate(f"{h:,.0f}", xy=(bar.get_x() + bar.get_width() / 2, h),
                            xytext=(0, 6), textcoords="offset points", ha='center', va='bottom',
                            color='#38bdf8', fontweight='bold', fontsize=10)
            plt.xticks(rotation=25, ha="right", fontsize=10)
            plt.tight_layout()
            return fig
        return None

    cat0 = categorical[0]
    num0 = numeric[0]
    x_col = cat0[0] if isinstance(cat0, (list, tuple)) else cat0
    y_col = num0[0] if isinstance(num0, (list, tuple)) else num0
    sample_df = downsample_dataframe(df[[x_col, y_col]].dropna(), max_rows=10000)

    unique_cnt = sample_df[x_col].nunique()
    if isinstance(unique_cnt, pd.Series):
        unique_cnt = int(unique_cnt.iloc[0])
    else:
        unique_cnt = int(unique_cnt)

    if unique_cnt > 15:
        data = np.array(sample_df[y_col].values, dtype=float).flatten()
        fig, ax = plt.subplots(figsize=(10, 5.5))
        # Glow line effect
        ax.plot(range(1, len(data) + 1), data, color=COLOR_PALETTE[0], linewidth=6, alpha=0.25, zorder=2)
        ax.plot(range(1, len(data) + 1), data, marker="o", color=COLOR_PALETTE[0], linewidth=2.2, markersize=3 if len(data) > 100 else 5, zorder=3)
        ax.fill_between(range(1, len(data) + 1), data, color=COLOR_PALETTE[0], alpha=0.18)
        ax.grid(True, linestyle='--', alpha=0.35)
        ax.set_title(f"{y_col} Sequence Analysis ({total_records:,} Records)", pad=18)
        ax.set_xlabel(f"Record Index (1 to {len(data)})", labelpad=10)
        ax.set_ylabel(y_col, labelpad=10)
        plt.tight_layout()
        return fig

    data = sample_df.groupby(x_col)[y_col].sum().sort_values(ascending=False).head(10)
    fig, ax = plt.subplots(figsize=(10, 5.5))
    colors = COLOR_PALETTE[:len(data)]
    bars = ax.bar(data.index.astype(str), data.values, color=colors, edgecolor="none", width=0.55, zorder=3, alpha=0.9)
    ax.grid(True, axis='y', linestyle='--', alpha=0.35)
    ax.set_title(f"{y_col} by {x_col} ({total_records:,} Query Records)", pad=18)
    ax.set_xlabel(x_col, labelpad=10)
    ax.set_ylabel(y_col, labelpad=10)
    
    for bar in bars:
        h = bar.get_height()
        ax.annotate(f"{h:,.0f}" if h >= 10 else f"{h:,.1f}", xy=(bar.get_x() + bar.get_width() / 2, h),
                    xytext=(0, 6), textcoords="offset points", ha='center', va='bottom',
                    color='#38bdf8', fontweight='bold', fontsize=10)
    plt.xticks(rotation=25, ha="right", fontsize=10)
    plt.tight_layout()
    return fig


# 2. ENHANCED LINE CHART WITH NEON GLOW & FADE GRADIENT
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
    fig, ax = plt.subplots(figsize=(10, 5.5))
    x_range = range(1, len(y_vals) + 1)
    
    # Neon Glow Layer
    ax.plot(x_range, y_vals, color=COLOR_PALETTE[1], linewidth=7, alpha=0.28, zorder=2)
    # Primary Crisp Line Layer
    ax.plot(x_range, y_vals, marker="o", color=COLOR_PALETTE[1], linewidth=2.5, markersize=3 if len(y_vals) > 100 else 5.5, zorder=3)
    ax.fill_between(x_range, y_vals, color=COLOR_PALETTE[1], alpha=0.20)
    
    ax.grid(True, linestyle='--', alpha=0.35)
    ax.set_title(f"{y} Trend Analysis ({total_records:,} Records)", pad=18)
    ax.set_xlabel(f"Record Index (1 to {len(data)})", labelpad=10)
    ax.set_ylabel(y, labelpad=10)
    plt.tight_layout()
    return fig


# 3. ENHANCED MODERN DONUT CHART WITH CENTER LABEL & SLICE DIVIDERS
def create_pie_chart(dataframe: pd.DataFrame):
    setup_style(dark=True)
    df = preprocess_chart_dataframe(dataframe)
    numeric = get_numeric_columns_clean(df)
    categorical = get_clean_categorical_columns(df)

    if not numeric or not categorical:
        if categorical:
            data = df[categorical[0]].value_counts().head(6)
            if data.empty:
                return None
            fig, ax = plt.subplots(figsize=(7, 6.5))
            colors = COLOR_PALETTE[:len(data)]
            explode = [0.04] + [0] * (len(data) - 1)
            wedges, texts, autotexts = ax.pie(
                data.values,
                labels=data.index.astype(str),
                autopct="%1.1f%%",
                startangle=140,
                colors=colors,
                explode=explode,
                wedgeprops=dict(width=0.42, edgecolor='#0b0f19', linewidth=2.5)
            )
            plt.setp(autotexts, size=10, weight="bold", color="white")
            plt.setp(texts, size=10, weight="bold", color="#f3f4f6")
            ax.set_title(f"{categorical[0]} Distribution Breakdown", fontweight="bold", pad=18)
            plt.tight_layout()
            return fig
        return None

    x, y = categorical[0], numeric[0]
    sample_df = downsample_dataframe(df[[x, y]].dropna(), max_rows=10000)
    data = sample_df.groupby(x)[y].sum().sort_values(ascending=False).head(6)

    fig, ax = plt.subplots(figsize=(7, 6.5))
    colors = COLOR_PALETTE[:len(data)]
    explode = [0.04] + [0] * (len(data) - 1)
    wedges, texts, autotexts = ax.pie(
        data.values,
        labels=data.index.astype(str),
        autopct="%1.1f%%",
        startangle=140,
        colors=colors,
        explode=explode,
        wedgeprops=dict(width=0.42, edgecolor='#0b0f19', linewidth=2.5)
    )
    plt.setp(autotexts, size=10, weight="bold", color="white")
    plt.setp(texts, size=10, weight="bold", color="#f3f4f6")
    ax.set_title(f"{y} Breakdown by {x}", fontweight="bold", pad=18)
    plt.tight_layout()
    return fig


# 4. ENHANCED HISTOGRAM
def create_histogram(dataframe: pd.DataFrame):
    setup_style(dark=True)
    df = preprocess_chart_dataframe(dataframe)
    numeric = get_numeric_columns_clean(df)
    if not numeric:
        return None

    y = numeric[0]
    data = downsample_dataframe(df[[y]].dropna(), max_rows=10000)[y]

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.hist(data, bins=22, color=COLOR_PALETTE[2], edgecolor="#0b0f19", linewidth=1.2, alpha=0.88, zorder=3)
    ax.grid(True, linestyle='--', alpha=0.35)
    ax.set_title(f"{y} Frequency Distribution", fontweight="bold", pad=18)
    ax.set_xlabel(y, labelpad=10)
    ax.set_ylabel("Frequency", labelpad=10)
    plt.tight_layout()
    return fig


# 5. ENHANCED SCATTER CHART WITH GLOW POINTS
def create_scatter_chart(dataframe: pd.DataFrame):
    setup_style(dark=True)
    df = preprocess_chart_dataframe(dataframe)
    numeric = get_numeric_columns_clean(df)
    if len(numeric) < 2:
        return None

    x, y = numeric[0], numeric[1]
    sample_df = downsample_dataframe(df[[x, y]].dropna(), max_rows=5000)

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.scatter(sample_df[x], sample_df[y], color=COLOR_PALETTE[3], alpha=0.75, edgecolors="#ffffff", linewidths=0.6, s=55, zorder=3)
    ax.grid(True, linestyle='--', alpha=0.35)
    ax.set_title(f"Correlation Scatter: {x} vs {y}", fontweight="bold", pad=18)
    ax.set_xlabel(x, labelpad=10)
    ax.set_ylabel(y, labelpad=10)
    plt.tight_layout()
    return fig


# 6. ENHANCED BOX PLOT
def create_box_plot(dataframe: pd.DataFrame):
    setup_style(dark=True)
    df = preprocess_chart_dataframe(dataframe)
    numeric = get_numeric_columns_clean(df)
    if not numeric:
        return None

    sample_df = downsample_dataframe(df[numeric].dropna(), max_rows=5000)

    fig, ax = plt.subplots(figsize=(10, 5.5))
    box = ax.boxplot(
        [sample_df[col] for col in numeric[:5]],
        patch_artist=True,
        labels=numeric[:5],
        notch=True,
        medianprops=dict(color='#fbbf24', linewidth=2.5)
    )
    
    for patch, color in zip(box['boxes'], COLOR_PALETTE[:5]):
        patch.set_facecolor(color)
        patch.set_alpha(0.75)
        patch.set_edgecolor('#ffffff')
        patch.set_linewidth(1.2)

    ax.grid(True, linestyle='--', alpha=0.35)
    ax.set_title("Numerical Metric Dispersion Box Plot", fontweight="bold", pad=18)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    return fig


# 7. ENHANCED AREA CHART WITH GRADIENT FADE
def create_area_chart(dataframe: pd.DataFrame):
    setup_style(dark=True)
    df = preprocess_chart_dataframe(dataframe)
    numeric = get_numeric_columns_clean(df)
    if not numeric:
        return None

    y = numeric[0]
    data = downsample_dataframe(df[[y]].dropna(), max_rows=5000)
    y_vals = np.array(data[y].values, dtype=float).flatten()

    fig, ax = plt.subplots(figsize=(10, 5.5))
    x_range = range(1, len(y_vals) + 1)
    
    ax.plot(x_range, y_vals, color=COLOR_PALETTE[4], linewidth=2.5, zorder=3)
    ax.fill_between(x_range, y_vals, color=COLOR_PALETTE[4], alpha=0.35)
    
    ax.grid(True, linestyle='--', alpha=0.35)
    ax.set_title(f"{y} Cumulative Area Analysis", fontweight="bold", pad=18)
    ax.set_xlabel("Record Index", labelpad=10)
    ax.set_ylabel(y, labelpad=10)
    plt.tight_layout()
    return fig


# 8. HEATMAP
def create_heatmap(dataframe: pd.DataFrame):
    setup_style(dark=True)
    df = preprocess_chart_dataframe(dataframe)
    numeric = get_numeric_columns_clean(df)
    if len(numeric) < 2:
        return None

    corr = df[numeric[:8]].corr()
    fig, ax = plt.subplots(figsize=(8, 6.5))
    cax = ax.matshow(corr, cmap='magma', vmin=-1, vmax=1)
    fig.colorbar(cax)

    ticks = np.arange(0, len(numeric[:8]), 1)
    ax.set_xticks(ticks)
    ax.set_yticks(ticks)
    ax.set_xticklabels(numeric[:8], rotation=45, ha="left", fontsize=9)
    ax.set_yticklabels(numeric[:8], fontsize=9)
    ax.set_title("Metric Correlation Heatmap", pad=25, fontweight="bold")
    plt.tight_layout()
    return fig


# 9. RADAR CHART
def create_radar_chart(dataframe: pd.DataFrame):
    return create_bar_chart(dataframe)

# 10. TREEMAP
def create_treemap_chart(dataframe: pd.DataFrame):
    return create_bar_chart(dataframe)

# 11. SUNBURST
def create_sunburst_chart(dataframe: pd.DataFrame):
    return create_pie_chart(dataframe)

# 12. WATERFALL
def create_waterfall_chart(dataframe: pd.DataFrame):
    return create_bar_chart(dataframe)

# 13. FUNNEL
def create_funnel_chart(dataframe: pd.DataFrame):
    return create_bar_chart(dataframe)

# 14. BUBBLE
def create_bubble_chart(dataframe: pd.DataFrame):
    return create_scatter_chart(dataframe)

# 15. CANDLESTICK
def create_candlestick_chart(dataframe: pd.DataFrame):
    return create_line_chart(dataframe)