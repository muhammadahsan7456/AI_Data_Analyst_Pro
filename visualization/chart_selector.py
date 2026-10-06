import pandas as pd
import re


def select_chart(dataframe: pd.DataFrame) -> str:
    """
    Automatically select optimal chart type based on DataFrame column types and cardinality.
    """
    if dataframe is None or dataframe.empty or dataframe.shape[1] < 1:
        return None

    df = dataframe.copy()

    # Preprocess string numbers & dates for accurate type detection
    for col in df.columns:
        col_lower = str(col).lower().strip()
        if col_lower in ["recordid", "id", "hash"] or col_lower.endswith("_id"):
            continue
        if not pd.api.types.is_numeric_dtype(df[col]):
            cleaned_s = df[col].astype(str).str.replace(r"[$,]", "", regex=True)
            converted = pd.to_numeric(cleaned_s, errors="coerce")
            if converted.notna().mean() > 0.5:
                df[col] = converted

    # Exclude technical IDs, tracking numbers, and row index/serial numbers from numeric metric list
    id_patterns = [
        r"^recordid$", r"^id$", r".*_id$", r"^key$", r"^tracking_id$",
        r"^sr$", r"^sr\.$", r"^sr_no$", r"^srno$", r"^s\.no$", r"^s_no$", r"^sno$",
        r"^index$", r"^row_num$", r"^row$", r"^#$", r"^sl_no$", r"^slno$",
        r"^consignment$", r"^tracking$", r"^cnic$", r"^cnic_number$", r"^phone$",
        r"^mobile$", r"^contact$", r"^consignee_contact$", r"^order_reference$", r"^order_ref$"
    ]
    numeric_columns = []
    for c in df.select_dtypes(include="number").columns:
        c_clean = str(c).lower().strip()
        if not any(re.match(p, c_clean) for p in id_patterns):
            valid_vals = df[c].dropna()
            if not valid_vals.empty:
                numeric_columns.append(c)

    categorical_columns = [c for c in df.columns if c not in numeric_columns and not any(re.match(p, str(c).lower().strip()) for p in id_patterns) and not pd.api.types.is_datetime64_any_dtype(df[c])]
    datetime_columns = df.select_dtypes(include="datetime").columns.tolist()

    # 1. Check for time-series / dates - only when there's an actual numeric value to plot
    # over time.
    date_cols = [c for c in df.columns if any(kw in str(c).lower() for kw in ["date", "time", "month", "year", "day", "week"])]
    if date_cols and numeric_columns and len(df) >= 3:
        return "line"

    # 2. Correlation Heatmap for multi-column numeric datasets
    if len(numeric_columns) >= 3 and len(categorical_columns) == 0:
        return "heatmap"

    # 3. Time series / Datetime present
    if datetime_columns and numeric_columns:
        return "line"

    # 4. Categorical Breakdown / Share Analysis
    if len(categorical_columns) >= 1:
        cat_col = categorical_columns[0]
        cat_col_lower = str(cat_col).lower()
        unique_cnt = df[cat_col].nunique()
        
        # Share & Distribution (Only for Status, Type, Flag, Gender, Binary distributions)
        is_status_or_type = any(kw in cat_col_lower for kw in ["status", "type", "flag", "service", "gender", "condition"])
        if is_status_or_type and 2 <= unique_cnt <= 6:
            return "pie"
        # Rankings & Top-N (High cardinality > 8 e.g. Top Destination Cities)
        elif unique_cnt > 8:
            return "horizontal_bar"
        return "bar"

    # 5. Multiple numeric columns
    if len(numeric_columns) >= 2:
        return "scatter"

    # 6. Default comparison
    return "bar"


def get_compatible_chart_types(dataframe: pd.DataFrame) -> list:
    """
    Return list of compatible chart types based on DataFrame column structure.
    """
    if dataframe is None or dataframe.empty:
        return ["table"]

    types = ["auto", "table", "bar", "horizontal_bar"]

    id_patterns = [r"^recordid$", r"^id$", r".*_id$", r"^key$", r"^sr$", r"^s\.no$", r"^sno$"]
    numeric_cols = [
        c for c in dataframe.select_dtypes(include="number").columns
        if not any(re.match(p, str(c).lower().strip()) for p in id_patterns)
    ]
    cat_cols = [c for c in dataframe.columns if c not in numeric_cols]

    if len(cat_cols) >= 1:
        types.extend(["pie", "donut", "treemap", "funnel"])

    if len(numeric_cols) >= 1:
        types.extend(["line", "area", "histogram", "boxplot"])

    if len(numeric_cols) >= 2:
        types.extend(["scatter", "heatmap"])

    return types