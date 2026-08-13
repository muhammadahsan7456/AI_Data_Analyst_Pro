import re
import pandas as pd


# Regex Patterns for PII Detection
EMAIL_REGEX = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
PHONE_REGEX = r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
CREDIT_CARD_REGEX = r"\b(?:\d[ -]*?){13,16}\b"
SSN_REGEX = r"\b\d{3}-\d{2}-\d{4}\b"

SENSITIVE_COLUMN_KEYWORDS = [
    "email", "mail", "phone", "mobile", "cell", "ssn", "social_security",
    "credit_card", "card_number", "national_id", "cnic", "passport", "password",
    "secret", "token", "cvv", "salary", "address"
]


def mask_email(email_str: str) -> str:
    """Mask email address (e.g. user@domain.com -> u***@domain.com)."""
    if not isinstance(email_str, str) or "@" not in email_str:
        return "[MASKED_EMAIL]"
    parts = email_str.split("@", 1)
    uname = parts[0]
    domain = parts[1] if len(parts) > 1 else ""
    first_char = uname[0] if uname else "u"
    return f"{first_char}***@{domain}"


def mask_phone(phone_str: str) -> str:
    """Mask phone number."""
    phone_digits = re.sub(r"\D", "", str(phone_str))
    if len(phone_digits) >= 4:
        return f"***-***-{phone_digits[-4:]}"
    return "***-***-****"


def mask_credit_card(card_str: str) -> str:
    """Mask credit card number."""
    card_digits = re.sub(r"\D", "", str(card_str))
    if len(card_digits) >= 4:
        return f"XXXX-XXXX-XXXX-{card_digits[-4:]}"
    return "XXXX-XXXX-XXXX-XXXX"


def mask_text_pii(text_val: str) -> str:
    """Detect and mask inline PII strings within unstructured text."""
    if not isinstance(text_val, str):
        return text_val

    # Email Masking
    text_val = re.sub(EMAIL_REGEX, lambda m: mask_email(m.group(0)), text_val)
    # Credit Card Masking
    text_val = re.sub(CREDIT_CARD_REGEX, lambda m: mask_credit_card(m.group(0)), text_val)
    # Phone Masking
    text_val = re.sub(PHONE_REGEX, lambda m: mask_phone(m.group(0)), text_val)
    # SSN Masking
    text_val = re.sub(SSN_REGEX, "[MASKED_SSN]", text_val)

    return text_val


def mask_dataframe_pii(df: pd.DataFrame) -> pd.DataFrame:
    """
    Scan columns and cell values in Pandas DataFrame and apply automatic PII masking.
    """
    if df is None or df.empty:
        return df

    masked_df = df.copy()

    for col in masked_df.columns:
        col_lower = str(col).lower()
        is_sensitive_col = any(keyword in col_lower for keyword in SENSITIVE_COLUMN_KEYWORDS)

        if is_sensitive_col:
            if "email" in col_lower or "mail" in col_lower:
                masked_df[col] = masked_df[col].astype(str).apply(mask_email)
            elif "phone" in col_lower or "mobile" in col_lower:
                masked_df[col] = masked_df[col].astype(str).apply(mask_phone)
            elif "card" in col_lower or "credit" in col_lower or "cvv" in col_lower:
                masked_df[col] = masked_df[col].astype(str).apply(mask_credit_card)
            else:
                masked_df[col] = "[MASKED_SENSITIVE]"
        elif masked_df[col].dtype == "object":
            # Mask inline strings
            masked_df[col] = masked_df[col].astype(str).apply(mask_text_pii)

    return masked_df


def sanitize_prompt_payload(question: str, df: pd.DataFrame, privacy_level: int = 3) -> str:
    """
    Enforce Enterprise AI Privacy Levels before constructing prompts sent to OpenRouter / Gemini API.
    Level 1: Maximum Privacy (Metadata & Schema Only, No rows)
    Level 2: Aggregated Data (Column Stats & Aggregates, No raw rows)
    Level 3: Default Enterprise (Masked Sample up to 20 rows max)
    Level 4: Developer Mode (Unmasked Sample up to 20 rows)
    """
    if df is None or df.empty:
        return "Dataset is empty."

    if privacy_level == 1:
        # Level 1: Schema & Metadata Only
        col_info = [f"- {col} ({df[col].dtype})" for col in df.columns]
        return f"Dataset Schema (Total Rows: {len(df)}):\n" + "\n".join(col_info)

    elif privacy_level == 2:
        # Level 2: Aggregated Statistics Only
        numeric_summary = df.describe(include="all").transpose().to_string()
        return f"Aggregated Dataset Summary (Total Rows: {len(df)}):\n{numeric_summary}"

    elif privacy_level == 3:
        # Level 3: Masked Sample Data (Max 20 rows)
        masked_df = mask_dataframe_pii(df.head(20))
        return f"Masked Sample Data (First {len(masked_df)} rows of {len(df)} total):\n" + masked_df.to_string(index=False)

    else:
        # Level 4: Developer Mode (Max 20 rows)
        sample_df = df.head(20)
        return f"Sample Data (First {len(sample_df)} rows of {len(df)} total):\n" + sample_df.to_string(index=False)
