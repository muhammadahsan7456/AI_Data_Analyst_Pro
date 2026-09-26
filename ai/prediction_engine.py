"""
Time-Series AI Forecasting & Prediction Engine
Validates dataset time suitability and generates 30-90 day trend projections with confidence ranges.
"""

import numpy as np
from database.connection import run_query
from ai.prediction import fit_linear_trend


def validate_time_series_suitability(table_name: str) -> dict:
    """
    Validates whether the dataset contains appropriate date columns and numeric target metrics.
    """
    try:
        df = run_query(f"SELECT TOP 500 * FROM [{table_name}]")
        if df is None or df.empty:
            return {"is_suitable": False, "reason": "Dataset is empty."}

        date_cols = [c for c in df.columns if any(kw in c.lower() for kw in ["date", "time", "year", "month", "subscribed", "created"])]
        numeric_cols = [c for c in df.select_dtypes(include=[np.number]).columns if c.lower() not in ["recordid", "index", "id"]]

        if not date_cols:
            return {
                "is_suitable": False,
                "reason": "This dataset does not contain enough suitable historical date/time information for a reliable forecast."
            }

        if not numeric_cols:
            # Fall back to record count over time
            return {
                "is_suitable": True,
                "date_column": date_cols[0],
                "metric_column": "Record_Count",
                "reason": "Sufficient historical date observations detected. Forecasting record volume."
            }

        return {
            "is_suitable": True,
            "date_column": date_cols[0],
            "metric_column": numeric_cols[0],
            "reason": f"Historical date column [{date_cols[0]}] and target metric [{numeric_cols[0]}] detected."
        }
    except Exception as e:
        return {"is_suitable": False, "reason": str(e)}


def generate_dataset_forecast(table_name: str, periods: int = 3) -> dict:
    """
    Generates time-series forecast projections with confidence intervals.
    """
    suitability = validate_time_series_suitability(table_name)
    if not suitability.get("is_suitable"):
        return {
            "success": False,
            "disclaimer": suitability.get("reason", "Dataset unsuitable for predictive modeling.")
        }

    date_col = suitability["date_column"]
    metric_col = suitability["metric_column"]

    try:
        if metric_col == "Record_Count":
            query = f"SELECT YEAR([{date_col}]) AS [Year], MONTH([{date_col}]) AS [Month], COUNT(*) AS [Metric] FROM [{table_name}] GROUP BY YEAR([{date_col}]), MONTH([{date_col}]) ORDER BY [Year] ASC, [Month] ASC;"
        else:
            query = f"SELECT YEAR([{date_col}]) AS [Year], MONTH([{date_col}]) AS [Month], SUM([{metric_col}]) AS [Metric] FROM [{table_name}] GROUP BY YEAR([{date_col}]), MONTH([{date_col}]) ORDER BY [Year] ASC, [Month] ASC;"

        ts_df = run_query(query)
        if ts_df is None or len(ts_df) < 3:
            return {
                "success": False,
                "disclaimer": "This dataset does not contain enough historical observations (minimum 3 time periods required) for a reliable forecast."
            }

        y_vals = ts_df["Metric"].astype(float).values

        # Linear Trend Fit with Confidence Bounds (shared authoritative model - see ai.prediction.fit_linear_trend)
        slope, intercept, std_err = fit_linear_trend(y_vals)

        future_x = np.arange(len(y_vals), len(y_vals) + periods)
        future_pred = slope * future_x + intercept

        forecast_items = []
        for i, val in enumerate(future_pred):
            pred_val = max(0, round(float(val), 2))
            lower_bound = max(0, round(pred_val - (1.96 * std_err), 2))
            upper_bound = round(pred_val + (1.96 * std_err), 2)
            forecast_items.append({
                "period": f"Period +{i+1}",
                "predicted": pred_val,
                "lower_bound": lower_bound,
                "upper_bound": upper_bound,
                "confidence_level": "95%"
            })

        trend_direction = "Increasing" if slope > 0 else "Decreasing" if slope < 0 else "Stable"

        return {
            "success": True,
            "metric": metric_col,
            "historical_periods": len(y_vals),
            "trend_direction": trend_direction,
            "slope": round(float(slope), 2),
            "model_used": "Linear Trend Regression with 95% Normal Confidence Intervals",
            "forecast": forecast_items,
            "disclaimer": "Forecasts represent mathematical projections based on historical dataset trends and do not guarantee future business performance."
        }
    except Exception as e:
        return {
            "success": False,
            "disclaimer": f"Forecasting model execution error: {str(e)}"
        }
