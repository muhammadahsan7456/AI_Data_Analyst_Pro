import re
import os
import sys

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from utils.logger import log_event
except ModuleNotFoundError:
    def log_event(*args, **kwargs): pass


def clean_voice_command(transcript: str) -> str:
    """
    Clean and normalize speech-to-text transcriptions.
    """
    if not transcript or not isinstance(transcript, str):
        return ""

    cleaned = transcript.strip().lower()

    # Remove common speech filler prefixes
    filler_prefixes = [
        r"^hey ai,?\s*",
        r"^assistant,?\s*",
        r"^please,?\s*",
        r"^can you,?\s*",
        r"^could you,?\s*",
        r"^ai,?\s*"
    ]
    for pat in filler_prefixes:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()

    return cleaned.capitalize()


def format_tts_response(question: str, df, explanation: str = None) -> str:
    """
    Format concise, clear natural speech response for Text-To-Speech (TTS) synthesis.
    Extracts the direct answer from the explanation so spoken audio answers the question.
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        return "The query returned no matching records in the dataset."

    # 1. If explanation contains a Direct Answer section, speak that directly
    if explanation and isinstance(explanation, str):
        match = re.search(r"(?:\*\*Direct Answer\*\*|Direct Answer)\s*:\s*([^\n\r]+)", explanation, re.IGNORECASE)
        if match:
            clean_ans = re.sub(r"[\*\#\`\_]", "", match.group(1)).strip()
            if len(clean_ans) >= 3:
                return clean_ans[:280]

        # Check for first bold sentence or leading insight
        lines = [line.strip() for line in explanation.split("\n") if line.strip()]
        for line in lines:
            if not line.startswith("#") and not line.startswith("|") and len(line) >= 15:
                clean_line = re.sub(r"[\*\#\`\_]", "", line).strip()
                if not clean_line.lower().startswith("executive") and not clean_line.lower().startswith("retrieved"):
                    return clean_line[:280]

    # 2. If single-cell result (e.g. aggregate count or sum), state it clearly
    if hasattr(df, "shape") and df.shape == (1, 1):
        col_name = str(df.columns[0]).replace("_", " ")
        val = df.iloc[0, 0]
        try:
            val_num = float(val)
            val_str = f"{int(val_num):,}" if val_num.is_integer() else f"{val_num:,.2f}"
        except (ValueError, TypeError):
            val_str = str(val)
        return f"The {col_name} is {val_str}."

    row_count = len(df) if hasattr(df, "__len__") else 0
    return f"Displaying {row_count} matching records for your question."
