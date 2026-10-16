import re

def format_user_friendly_error(raw_error) -> str:
    """
    Translates technical database (ODBC, SQL Server, Python) exceptions
    into clean, executive-ready, business-understandable error messages.
    """
    if not raw_error:
        return "An unexpected error occurred. Please try again."

    err_str = str(raw_error).strip()
    err_lower = err_str.lower()

    # 1. Structural Mismatch (UNION / Column Count mismatch)
    if "union" in err_lower or "equal number of expressions" in err_lower or "target lists" in err_lower:
        return "Dataset Structure Mismatch: The selected datasets have different numbers of columns (e.g. 25 columns vs 5 columns). Please select datasets with matching fields, or specify common columns to compare."

    # 2. Invalid Column Name
    if "invalid column name" in err_lower:
        match = re.search(r"invalid column name '([^']+)'", err_str, re.IGNORECASE)
        col_name = f" '{match.group(1)}'" if match else ""
        return f"Column Not Found: Column{col_name} does not exist in the selected dataset. Please check available column headers."

    # 3. Table or Object Not Found / Unavailable
    if "invalid object name" in err_lower or "no such table" in err_lower:
        return "Dataset Unavailable: The selected dataset table is currently updating or unavailable. Please re-select the dataset and try again."

    # 4. Data Type / Calculation Error
    if "conversion failed" in err_lower or "arithmetic overflow" in err_lower or "datatype" in err_lower:
        return "Data Type Mismatch: Unable to perform mathematical calculations on text/string columns. Please ensure you are aggregating numeric fields."

    # 5. Syntax / T-SQL Parsing Error
    if "incorrect syntax" in err_lower or "syntax error" in err_lower:
        return "Query Syntax Notice: The AI SQL Query could not be processed. Please rephrase your question in natural English or Roman Urdu."

    # 6. Generic ODBC Driver / SQL Server Error Cleanup - never echo the raw driver
    # message back to the browser (it can contain table/column names or server
    # detail); log it server-side instead and show a safe generic notice.
    if "odbc driver" in err_lower or "sqlexecdirectw" in err_lower or "pyodbc" in err_lower:
        print("Database Notice (raw, server-side only):", err_str)
        return "Database Notice: Unable to complete database operation. Please refine your query."

    # Default: anything not matched above is an unanticipated error shape, so it
    # is never safe to echo to the browser - log it server-side and return a
    # generic notice instead of the raw/cleaned exception text.
    print("Unhandled query error (raw, server-side only):", err_str)
    return "Notice: We couldn't process that request. Please rephrase your question and try again."
