import os
import re
import time
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from database.connection import run_query, sanitize_identifier, get_db_cursor
from utils.cache import system_cache


def detect_time_and_target_columns(df: pd.DataFrame):
    """
    Automatically identify Date/Time column and primary target metric column in a DataFrame.
    """
    date_col = None
    target_col = None

    if df is None or df.empty:
        return None, None

    # Detect Date Column
    for col in df.columns:
        col_lower = str(col).lower()
        if any(kw in col_lower for kw in ["date", "time", "year", "month", "day", "created", "upload", "timestamp"]):
            date_col = col
            break
        elif df[col].dtype == "datetime64[ns]":
            date_col = col
            break

    if not date_col:
        for col in df.columns:
            if df[col].dtype == "object":
                try:
                    pd.to_datetime(df[col].head(20), errors="raise")
                    date_col = col
                    break
                except Exception:
                    pass

    # Detect Primary Target Metric Column
    priority_target_keywords = ["sales", "revenue", "profit", "amount", "total", "count", "orders", "customers", "traffic", "quantity"]
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    
    # Exclude IDs or Record numbers
    numeric_cols = [c for c in numeric_cols if not any(id_kw in str(c).lower() for id_kw in ["id", "code", "year", "zip", "number"])]

    for kw in priority_target_keywords:
        for col in numeric_cols:
            if kw in str(col).lower():
                target_col = col
                break
        if target_col:
            break

    if not target_col and numeric_cols:
        target_col = numeric_cols[0]

    return date_col, target_col


def generate_forecast(df: pd.DataFrame, target_col: str = None, date_col: str = None, forecast_days: int = 30, algorithm: str = "auto") -> dict:
    """
    Generate professional time-series prediction, confidence intervals, anomaly detection,
    and high-resolution forecasting chart.
    """
    if df is None or df.empty:
        return {"success": False, "error": "Dataset is empty."}

    # Auto-detect columns if not specified
    det_date, det_target = detect_time_and_target_columns(df)
    date_col = date_col or det_date
    target_col = target_col or det_target

    if not target_col:
        return {"success": False, "error": "No suitable numeric column found for prediction."}

    clean_df = df.copy()

    # Handle Date Indexing
    if date_col and date_col in clean_df.columns:
        try:
            clean_df["_parsed_date"] = pd.to_datetime(clean_df[date_col], errors="coerce")
            clean_df = clean_df.dropna(subset=["_parsed_date"])
            clean_df = clean_df.sort_values("_parsed_date")
            ts_series = clean_df.set_index("_parsed_date")[target_col]
        except Exception:
            ts_series = clean_df[target_col].dropna()
    else:
        ts_series = clean_df[target_col].dropna()

    if len(ts_series) < 3:
        return {"success": False, "error": f"Insufficient data points in '{target_col}' for forecasting (minimum 3 required)."}

    # Resample or aggregate if datetime index exists
    if isinstance(ts_series.index, pd.DatetimeIndex):
        ts_series = ts_series.resample("D").mean().ffill().bfill()

    y_vals = ts_series.values.astype(float)
    x_vals = np.arange(len(y_vals))

    # Fit Trend Model (Linear & Exponential Moving Average)
    slope, intercept = np.polyfit(x_vals, y_vals, 1)
    
    # Generate Future X values
    future_x = np.arange(len(y_vals), len(y_vals) + forecast_days)
    trend_future = slope * future_x + intercept

    # Exponential Smoothing for seasonality adjustment
    alpha = 0.3
    smoothed = [y_vals[0]]
    for t in range(1, len(y_vals)):
        smoothed.append(alpha * y_vals[t] + (1 - alpha) * smoothed[-1])
    last_smoothed = smoothed[-1]

    # Combine trend + smoothing
    forecast_vals = (trend_future + last_smoothed) / 2.0
    forecast_vals = np.maximum(0, forecast_vals)  # Non-negative prediction guard

    # Confidence Intervals (95% CI)
    residuals = y_vals - (slope * x_vals + intercept)
    std_err = np.std(residuals) if len(residuals) > 0 else 1.0
    margin_of_error = 1.96 * std_err * (1 + (np.arange(forecast_days) / float(forecast_days)))

    upper_bound = forecast_vals + margin_of_error
    lower_bound = np.maximum(0, forecast_vals - margin_of_error)

    # Anomaly Detection on Historical Data (IQR Method)
    q25, q75 = np.percentile(y_vals, 25), np.percentile(y_vals, 75)
    iqr = q75 - q25
    lower_iqr, upper_iqr = q25 - 1.5 * iqr, q75 + 1.5 * iqr
    anomalies_count = int(np.sum((y_vals < lower_iqr) | (y_vals > upper_iqr)))

    # Dates calculation
    if isinstance(ts_series.index, pd.DatetimeIndex):
        last_date = ts_series.index[-1]
        future_dates = [last_date + pd.Timedelta(days=i + 1) for i in range(forecast_days)]
        future_date_strs = [d.strftime("%Y-%m-%d") for d in future_dates]
        hist_date_strs = [d.strftime("%Y-%m-%d") for d in ts_series.index[-50:]]
    else:
        future_date_strs = [f"Day +{i+1}" for i in range(forecast_days)]
        hist_date_strs = [f"Point {i+1}" for i in range(min(50, len(y_vals)))]

    # Generate Forecast Visualization Chart
    chart_filename = f"forecast_{int(time.time())}_{hash(target_col)}.png"
    os.makedirs("static/charts", exist_ok=True)
    chart_path = os.path.join("static", "charts", chart_filename)

    fig, ax = plt.subplots(figsize=(10, 5), dpi=120)
    fig.patch.set_facecolor("#1e293b")
    ax.set_facecolor("#0f172a")

    # Plot historical data
    hist_y = y_vals[-50:]
    ax.plot(range(len(hist_y)), hist_y, color="#38bdf8", linewidth=2.5, label="Historical Data")

    # Plot forecast
    forecast_x_range = range(len(hist_y), len(hist_y) + forecast_days)
    ax.plot(forecast_x_range, forecast_vals, color="#a855f7", linewidth=2.5, linestyle="--", label=f"{forecast_days}-Day AI Forecast")
    
    # Fill confidence interval band
    ax.fill_between(
        forecast_x_range,
        lower_bound,
        upper_bound,
        color="#a855f7",
        alpha=0.15,
        label="95% Confidence Interval"
    )

    ax.set_title(f"AI Prediction & Time-Series Forecast: [{target_col}]", fontsize=14, color="#f8fafc", fontweight="bold", pad=15)
    ax.set_xlabel("Time Horizon", color="#94a3b8", fontsize=11)
    ax.set_ylabel(f"Value ({target_col})", color="#94a3b8", fontsize=11)
    ax.tick_params(colors="#94a3b8", labelsize=9)
    ax.grid(True, linestyle=":", color="#334155", alpha=0.6)
    ax.legend(facecolor="#1e293b", edgecolor="#334155", labelcolor="#f8fafc", loc="upper left")

    plt.tight_layout()
    plt.savefig(chart_path, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close("all")

    # Summary Statistics & Business Growth
    avg_hist = float(np.mean(y_vals))
    avg_forecast = float(np.mean(forecast_vals))
    pct_growth = round(((avg_forecast - avg_hist) / (avg_hist + 1e-9)) * 100, 2)
    trend_direction = "Upward (Growth)" if pct_growth > 0 else ("Downward (Decline)" if pct_growth < 0 else "Stable")

    return {
        "success": True,
        "target_col": target_col,
        "date_col": date_col or "Row Index",
        "forecast_days": forecast_days,
        "algorithm_used": "Linear Trend + Exponential Smoothing (EMA)",
        "trend_direction": trend_direction,
        "pct_growth": pct_growth,
        "avg_historical": round(avg_hist, 2),
        "avg_forecast": round(avg_forecast, 2),
        "forecast_total": round(float(np.sum(forecast_vals)), 2),
        "anomalies_detected": anomalies_count,
        "confidence_level": "95%",
        "chart_path": f"/static/charts/{chart_filename}",
        "forecast_preview": list(zip(future_date_strs[:10], [round(v, 2) for v in forecast_vals[:10]]))
    }


def log_prediction_run(user_id: int, dataset_id: int, target_col: str, forecast_days: int, result_summary: str):
    """Save prediction audit record in database."""
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("""
            INSERT INTO AuditLogs (UserID, Action, Details, CreatedAt)
            VALUES (?, 'RUN_PREDICTION', ?, GETDATE())
            """, (user_id, f"Dataset #{dataset_id} target '{target_col}' for {forecast_days} days. {result_summary}"))
    except Exception as e:
        print("Prediction Audit Notice:", e)
