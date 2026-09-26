import re
import time
from contextlib import contextmanager


def is_safe_select_query(query: str) -> bool:
    """
    Ensure SQL query strictly performs SELECT operations and contains no destructive DDL/DML.
    """
    if not query or not isinstance(query, str):
        return False

    trimmed = query.strip()
    if not re.match(r"^\s*SELECT\b", trimmed, re.IGNORECASE):
        return False

    forbidden_keywords = [
        r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bDROP\b",
        r"\bALTER\b", r"\bTRUNCATE\b", r"\bEXEC\b", r"\bEXECUTE\b",
        r"\bCREATE\b", r"\bGRANT\b", r"\bREVOKE\b"
    ]

    for kw in forbidden_keywords:
        if re.search(kw, trimmed, re.IGNORECASE):
            return False

    return True


def format_bytes(bytes_count: float) -> str:
    """
    Format byte size into human readable string (KB, MB, GB).
    """
    if bytes_count < 1024:
        return f"{bytes_count:.2f} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.2f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.2f} MB"
    else:
        return f"{bytes_count / (1024 * 1024 * 1024):.2f} GB"


def format_12hr_datetime(val) -> str:
    """
    Format ISO / SQL timestamp into 12-Hour AM/PM format (e.g. 'Aug 16, 2026, 02:30 PM').
    """
    if not val:
        return ""
    val_str = str(val).strip()
    if not val_str:
        return ""

    try:
        from datetime import datetime
        if isinstance(val, datetime):
            return val.strftime("%b %d, %Y, %I:%M %p")

        val_clean = val_str.replace("Z", "").split("+")[0]
        if "." in val_clean:
            dt = datetime.strptime(val_clean.split(".")[0], "%Y-%m-%d %H:%M:%S")
        elif "T" in val_clean:
            dt = datetime.strptime(val_clean, "%Y-%m-%dT%H:%M:%S")
        else:
            dt = datetime.strptime(val_clean, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%b %d, %Y, %I:%M %p")
    except Exception:
        try:
            from datetime import datetime
            dt = datetime.strptime(val_str[:10], "%Y-%m-%d")
            return dt.strftime("%b %d, %Y")
        except Exception:
            return val_str


@contextmanager
def timer():
    """
    Execution timer context manager.
    """
    start = time.perf_counter()
    res = {}
    try:
        yield res
    finally:
        res["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)


CITY_CODE_MAP = {
    "KHI": "Karachi",
    "LHE": "Lahore",
    "ISB": "Islamabad",
    "RWP": "Rawalpindi",
    "MUX": "Multan",
    "PEW": "Peshawar",
    "FSD": "Faisalabad",
    "UET": "Quetta",
    "HDD": "Hyderabad",
    "SKZ": "Sukkur",
    "BWP": "Bahawalpur",
    "NWS": "Nawabshah",
    "TLG": "Talagang",
    "NOW": "Nowshera",
    "KOT": "Kotli",
    "DRG": "Dera Ghazi Khan",
    "JHG": "Jhang",
    "DNR": "Dina",
    "GJR": "Gujranwala",
    "SKT": "Sialkot",
    "GUJ": "Gujrat",
    "SWL": "Sahiwal",
    "MIR": "Mirpur",
    "ABT": "Abbottabad"
}


def expand_city_names_in_df(df):
    """
    Expands 3-letter city airport codes (e.g. KHI -> Karachi, LHE -> Lahore, ISB -> Islamabad)
    to full human-readable city names in dataframes.
    """
    if df is None or df.empty:
        return df

    import pandas as pd
    df_copy = df.copy()
    for col in df_copy.columns:
        col_low = str(col).lower()
        if any(kw in col_low for kw in ["city", "origin", "destination", "location", "address"]):
            df_copy[col] = df_copy[col].apply(lambda x: f"{CITY_CODE_MAP[str(x).upper().strip()]} ({str(x).upper().strip()})" if (pd.notnull(x) and str(x).upper().strip() in CITY_CODE_MAP) else x)

    return df_copy


def generate_dynamic_prompt_suggestions(columns: list) -> list:
    """
    Generates domain-aware smart prompt suggestion pills tailored to the active dataset schema.
    Returns a list of tuples: [("Icon Label", "Full Prompt Text")]
    """
    if not columns:
        return [
            ("📊 Summary Stats", "Show overall summary statistics"),
            ("🔍 Top 10 Rows", "Show top 10 rows from dataset"),
            ("📈 Data Distribution", "Show breakdown of key metrics")
        ]

    cols_low = [c.lower() for c in columns]

    # Check for Logistics / Courier / Fulfillment Domain
    is_logistics = any(kw in c for c in cols_low for kw in ["destination", "origin", "status", "delivery", "consignee", "courier", "rto"])
    # Check for Sales / E-Commerce Domain
    is_sales = any(kw in c for c in cols_low for kw in ["sales", "revenue", "product", "item", "customer", "price", "amount"])

    if is_logistics:
        return [
            ("🚚 Delivered vs Return", "Delivered orders aur return orders count batao"),
            ("📍 Lahore Returns", "Lahore ke return orders count batao"),
            ("🏙️ Sub-Area Breakdown", "Lahore mein returns kaun kaun se areas se hue hain"),
            ("📦 Top Destination Cities", "Show top 10 destination cities by order count"),
            ("📉 High Return Cities", "Which cities have the highest return rate"),
            ("📊 Auto Chart", "Generate best interactive chart for dataset")
        ]
    elif is_sales:
        return [
            ("💰 Total Revenue", "What is the total revenue amount"),
            ("🛍️ Top 10 Selling Products", "Show top 10 products by sales revenue"),
            ("💳 Average Order Value", "Calculate average order value"),
            ("📈 Sales Breakdown", "Show revenue breakdown by category"),
            ("📊 Auto Chart", "Generate best interactive chart for dataset")
        ]
    else:
        return [
            ("📊 Column Summary", "Show summary statistics for key columns"),
            ("🔍 Top 10 Records", "Show top 10 records from dataset"),
            ("📈 Distribution Breakdown", "Show breakdown of categorical metrics"),
            ("📊 Auto Chart", "Generate best interactive chart for dataset")
        ]
