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

    # Exclude technical IDs and row index/serial numbers from numeric metric list
    id_patterns = [r"^recordid$", r"^id$", r".*_id$", r"^key$", r"^tracking_id$", r"^sr$", r"^s\.no$", r"^s_no$", r"^sno$", r"^index$", r"^row_num$"]
    numeric_columns = [
        c for c in df.select_dtypes(include="number").columns
        if not any(re.match(p, str(c).lower().strip()) for p in id_patterns)
    ]
    categorical_columns = [c for c in df.columns if c not in numeric_columns and not pd.api.types.is_datetime64_any_dtype(df[c])]
    datetime_columns = df.select_dtypes(include="datetime").columns.tolist()

    # 1. Correlation Heatmap for multi-column numeric datasets
    if len(numeric_columns) >= 3 and len(categorical_columns) == 0:
        return "heatmap"

    # 2. Time series / Datetime present
    if datetime_columns and numeric_columns:
        return "line"

    # 3. Categorical + Numeric
    if len(categorical_columns) >= 1 and len(numeric_columns) >= 1:
        unique_cnt = df[categorical_columns[0]].nunique()
        if 2 <= unique_cnt <= 6:
            return "pie"
        return "bar"

    # 4. Multiple numeric columns
    if len(numeric_columns) >= 2:
        return "scatter"

    # 5. Single numeric column
    if len(numeric_columns) == 1:
        if len(df) > 30:
            return "histogram"
        return "bar"

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
        types.extend(["pie", "donut"])

    if len(numeric_cols) >= 1:
        types.extend(["line", "area", "histogram", "boxplot"])

    if len(numeric_cols) >= 2:
        types.extend(["scatter", "heatmap"])

    return types