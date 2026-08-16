import os
import sys
import pandas as pd
from dotenv import load_dotenv

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

load_dotenv()

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

_FERNET_CIPHER = None


def _get_cipher():
    global _FERNET_CIPHER
    if _FERNET_CIPHER is not None:
        return _FERNET_CIPHER

    if Fernet is None:
        return None

    raw_key = os.getenv("DATASET_ENCRYPTION_KEY", "").strip()
    if not raw_key:
        raw_key = Fernet.generate_key().decode()
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        try:
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(f"\nDATASET_ENCRYPTION_KEY={raw_key}\n")
        except Exception:
            pass

    try:
        _FERNET_CIPHER = Fernet(raw_key.encode())
    except Exception:
        key_bytes = Fernet.generate_key()
        _FERNET_CIPHER = Fernet(key_bytes)

    return _FERNET_CIPHER


def encrypt_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Encrypt all text/string columns with AES-256 Fernet Encryption at rest before saving to SQL Server.
    Numeric metrics, IDs, and timestamps remain native for SQL analytical aggregations.
    """
    if df is None or df.empty:
        return df

    cipher = _get_cipher()
    if not cipher:
        return df

    df_enc = df.copy()
    for col in df_enc.columns:
        c_lower = str(col).lower().strip()
        if c_lower in ["recordid", "s.no"]:
            continue
        
        # Target string/text/category columns in any pandas version
        if pd.api.types.is_string_dtype(df_enc[col]) or str(df_enc[col].dtype) in ["object", "string", "category", "str"]:
            def _enc_val(x):
                if x is None or pd.isna(x):
                    return None
                x_str = str(x).strip()
                if not x_str or x_str.startswith("enc:"):
                    return x_str
                try:
                    return "enc:" + cipher.encrypt(x_str.encode("utf-8")).decode("utf-8")
                except Exception:
                    return x_str
            df_enc[col] = df_enc[col].astype(str).apply(_enc_val)

    return df_enc


def decrypt_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Transparently decrypt encrypted enc: ciphertext fields for authorized user frontend views.
    """
    if df is None or df.empty:
        return df

    cipher = _get_cipher()
    if not cipher:
        return df

    df_dec = df.copy()
    for col in df_dec.columns:
        if pd.api.types.is_string_dtype(df_dec[col]) or str(df_dec[col].dtype) in ["object", "string", "category", "str"]:
            def _dec_val(x):
                if x is None or pd.isna(x):
                    return None
                x_str = str(x)
                if x_str.startswith("enc:"):
                    try:
                        return cipher.decrypt(x_str[4:].encode("utf-8")).decode("utf-8")
                    except Exception:
                        return x_str
                return x_str
            df_dec[col] = df_dec[col].astype(str).apply(_dec_val)

    return df_dec
