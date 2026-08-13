import re
import os
import sys
from typing import Dict, Any

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
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        return "The query returned no matching records in the dataset."

    row_count = len(df) if hasattr(df, "__len__") else 0
    return f"Query executed successfully. Displaying {row_count} matching records."
