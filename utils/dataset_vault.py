"""
Encrypted dataset storage ("vault").

What it does
------------
Every uploaded dataset's rows are stored ONLY as encrypted chunks in the `DatasetVault`
table. The dataset's own SQL table (e.g. `sales_u12`) is still created with the right
columns and types, so schema lookups keep working, but it holds no rows. Anyone who opens
the database (a DBA, a backup, the Super Admin with SQL access) sees column names and
ciphertext, never the values.

How queries still work
----------------------
`database.connection.run_query()` checks whether a query reads a vaulted table. If it does,
the owner's rows are decrypted into a private in-memory SQLite database (never written to
disk) and the query runs there, after the same T-SQL -> SQLite translation the QA/dev
SQLite mode already uses. Decrypted copies are cached in memory for a short time
(`DATASET_VAULT_CACHE_TTL_SECONDS`, default 900) and bounded by
`DATASET_VAULT_CACHE_MAX_ROWS` (default 3,000,000 rows across all cached datasets).

Keys
----
* Each dataset gets its own random 256-bit data key (AES-256-GCM).
* That data key is stored only in wrapped form, encrypted with the server master key
  `DATASET_ENCRYPTION_KEY` (a Fernet key in the server's environment, never in the DB).
* Each chunk is a Parquet file encrypted with AES-256-GCM; the table name and chunk number
  are authenticated (AAD), so chunks cannot be swapped between datasets unnoticed.

Limits (stated plainly)
-----------------------
The application server has to read the data to answer questions about it, so someone who
holds BOTH the database AND the server's `DATASET_ENCRYPTION_KEY` can decrypt. The database
alone, a backup alone, or the admin console reveal nothing. Column names are not encrypted.
"""
import io
import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict

import pandas as pd

log = logging.getLogger(__name__)

VAULT_TABLE = "DatasetVault"
META_TABLE = "DatasetVaultMeta"
CHUNK_ROWS = 100_000

_schema_ready = False
_schema_lock = threading.Lock()
_meta_cache = {}  # table_name_lower -> (vaulted: bool, checked_at: float)
_NEGATIVE_TTL = 60  # "not vaulted" is re-checked after this, so a migration run by another process is picked up
_meta_lock = threading.Lock()


class VaultUnavailable(RuntimeError):
    """Raised when encryption is required but no valid master key is configured."""


# --------------------------------------------------------------------------- settings

def vault_enabled() -> bool:
    """New uploads go to the vault unless explicitly disabled (DATASET_VAULT=off)."""
    return os.getenv("DATASET_VAULT", "on").strip().lower() not in ("0", "off", "false", "no")


def _master():
    try:
        from cryptography.fernet import Fernet
    except ImportError:  # pragma: no cover
        return None
    raw = os.getenv("DATASET_ENCRYPTION_KEY", "").strip()
    if not raw:
        return None
    try:
        return Fernet(raw.encode())
    except Exception:
        return None


def master_key_ok() -> bool:
    return _master() is not None


# --------------------------------------------------------------------------- db helpers

def _conn():
    from database.connection import get_connection
    return get_connection()


def _is_sqlite(conn) -> bool:
    return type(conn).__name__ == "SQLiteConnectionAdapter"


def ensure_schema():
    """Create the two vault tables if missing. Additive and idempotent."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        conn = _conn()
        try:
            cur = conn.cursor()
            if _is_sqlite(conn):
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {META_TABLE} (
                    TableName TEXT PRIMARY KEY, WrappedKey BLOB NOT NULL, KeyVersion INTEGER NOT NULL DEFAULT 1,
                    TotalRows INTEGER NOT NULL DEFAULT 0, ChunkCount INTEGER NOT NULL DEFAULT 0,
                    CreatedAt TEXT DEFAULT CURRENT_TIMESTAMP)""")
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {VAULT_TABLE} (
                    TableName TEXT NOT NULL, ChunkNo INTEGER NOT NULL, Payload BLOB NOT NULL,
                    PRIMARY KEY (TableName, ChunkNo))""")
            else:
                cur.execute(f"""
                IF OBJECT_ID('{META_TABLE}', 'U') IS NULL
                CREATE TABLE {META_TABLE} (
                    TableName NVARCHAR(128) NOT NULL PRIMARY KEY, WrappedKey VARBINARY(MAX) NOT NULL,
                    KeyVersion INT NOT NULL DEFAULT 1, TotalRows BIGINT NOT NULL DEFAULT 0,
                    ChunkCount INT NOT NULL DEFAULT 0, CreatedAt DATETIME2 DEFAULT SYSUTCDATETIME());
                IF OBJECT_ID('{VAULT_TABLE}', 'U') IS NULL
                CREATE TABLE {VAULT_TABLE} (
                    TableName NVARCHAR(128) NOT NULL, ChunkNo INT NOT NULL, Payload VARBINARY(MAX) NOT NULL,
                    CONSTRAINT PK_{VAULT_TABLE} PRIMARY KEY (TableName, ChunkNo));
                """)
            conn.commit()
            _schema_ready = True
        finally:
            conn.close()


def _norm(table_name: str) -> str:
    return str(table_name or "").strip().strip("[]").lower()


def is_vaulted(table_name: str) -> bool:
    key = _norm(table_name)
    if not key:
        return False
    with _meta_lock:
        hit = _meta_cache.get(key)
        if hit and (hit[0] or time.time() - hit[1] < _NEGATIVE_TTL):
            return hit[0]
    try:
        ensure_schema()
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {META_TABLE} WHERE LOWER(TableName) = ?", (key,))
            row = cur.fetchone()
            found = bool(row and row[0])
        finally:
            conn.close()
    except Exception:
        return False
    with _meta_lock:
        _meta_cache[key] = (found, time.time())
    return found


# --------------------------------------------------------------------------- crypto

def _aad(table_name: str, chunk_no: int) -> bytes:
    return f"{_norm(table_name)}:{chunk_no}".encode()


def _new_wrapped_key():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    m = _master()
    if m is None:
        raise VaultUnavailable("DATASET_ENCRYPTION_KEY is missing or invalid; datasets cannot be stored encrypted.")
    dk = AESGCM.generate_key(bit_length=256)
    return m.encrypt(dk)


def _unwrap(wrapped: bytes) -> bytes:
    m = _master()
    if m is None:
        raise VaultUnavailable("DATASET_ENCRYPTION_KEY is missing or invalid; encrypted datasets cannot be read.")
    return m.decrypt(bytes(wrapped))


def _encrypt_chunk(dk: bytes, table_name: str, chunk_no: int, df: pd.DataFrame) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    nonce = os.urandom(12)
    return b"V1" + nonce + AESGCM(dk).encrypt(nonce, buf.getvalue(), _aad(table_name, chunk_no))


def _decrypt_chunk(dk: bytes, table_name: str, chunk_no: int, payload: bytes) -> pd.DataFrame:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    payload = bytes(payload)
    if payload[:2] != b"V1":
        raise ValueError("Unknown vault chunk format")
    nonce, body = payload[2:14], payload[14:]
    raw = AESGCM(dk).decrypt(nonce, body, _aad(table_name, chunk_no))
    return pd.read_parquet(io.BytesIO(raw))


# --------------------------------------------------------------------------- write

def register_table(table_name: str):
    """Mark a freshly created (empty) dataset table as vaulted, with a new data key."""
    ensure_schema()
    wrapped = _new_wrapped_key()
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {VAULT_TABLE} WHERE LOWER(TableName) = ?", (_norm(table_name),))
        cur.execute(f"DELETE FROM {META_TABLE} WHERE LOWER(TableName) = ?", (_norm(table_name),))
        cur.execute(f"INSERT INTO {META_TABLE} (TableName, WrappedKey, KeyVersion, TotalRows, ChunkCount) VALUES (?, ?, 1, 0, 0)",
                    (_norm(table_name), wrapped))
        conn.commit()
    finally:
        conn.close()
    with _meta_lock:
        _meta_cache[_norm(table_name)] = (True, time.time())
    invalidate(table_name)


def _shell_columns(table_name: str) -> list:
    from database.connection import get_table_columns
    try:
        from utils.cache import system_cache
        if system_cache:
            system_cache.delete(f"tbl_cols_{_norm(table_name)}")
    except Exception:
        pass
    return get_table_columns(table_name)


def append_rows(table_name: str, df: pd.DataFrame) -> int:
    """Encrypt and append rows. RecordID is assigned here (continuing across chunks)."""
    if df is None or df.empty:
        return 0
    ensure_schema()
    shell = _shell_columns(table_name)
    data_cols = [c for c in shell if c.lower() != "recordid"]
    out = df.copy()
    if len(data_cols) == len(out.columns):
        out.columns = data_cols
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT WrappedKey, TotalRows, ChunkCount FROM {META_TABLE} WHERE LOWER(TableName) = ?", (_norm(table_name),))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"{table_name} is not registered in the dataset vault")
        dk = _unwrap(row[0])
        total, chunks = int(row[1] or 0), int(row[2] or 0)
        out.insert(0, "RecordID", range(total + 1, total + len(out) + 1))
        # Parquet needs one type per column; object columns with mixed values become text.
        for c in out.columns:
            if str(out[c].dtype) == "float32":
                out[c] = out[c].astype("float64")
            if out[c].dtype == object:
                out[c] = out[c].map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
        written = 0
        for start in range(0, len(out), CHUNK_ROWS):
            part = out.iloc[start:start + CHUNK_ROWS]
            cur.execute(f"INSERT INTO {VAULT_TABLE} (TableName, ChunkNo, Payload) VALUES (?, ?, ?)",
                        (_norm(table_name), chunks, _encrypt_chunk(dk, table_name, chunks, part)))
            chunks += 1
            written += len(part)
        cur.execute(f"UPDATE {META_TABLE} SET TotalRows = ?, ChunkCount = ? WHERE LOWER(TableName) = ?",
                    (total + written, chunks, _norm(table_name)))
        conn.commit()
    finally:
        conn.close()
    invalidate(table_name)
    return written


def drop(table_name: str, cursor=None):
    """Remove a dataset's encrypted rows and key (when the dataset is deleted).

    Pass the caller's open cursor to delete inside its transaction; without one, a
    separate connection is used (call it only after the caller's transaction committed,
    or it can wait on that transaction's locks)."""
    if cursor is not None:
        ensure_schema()
        cursor.execute(f"DELETE FROM {VAULT_TABLE} WHERE LOWER(TableName) = ?", (_norm(table_name),))
        cursor.execute(f"DELETE FROM {META_TABLE} WHERE LOWER(TableName) = ?", (_norm(table_name),))
        with _meta_lock:
            _meta_cache.pop(_norm(table_name), None)
        invalidate(table_name)
        return
    try:
        ensure_schema()
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM {VAULT_TABLE} WHERE LOWER(TableName) = ?", (_norm(table_name),))
            cur.execute(f"DELETE FROM {META_TABLE} WHERE LOWER(TableName) = ?", (_norm(table_name),))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.error("Vault cleanup failed for a deleted dataset: %s", type(e).__name__)
    with _meta_lock:
        _meta_cache.pop(_norm(table_name), None)
    invalidate(table_name)


# --------------------------------------------------------------------------- read (in-memory mirror)

class _Mirror:
    def __init__(self, conn, rows):
        self.conn = conn
        self.rows = rows
        self.loaded = time.time()
        self.lock = threading.Lock()


_mirrors = OrderedDict()  # table_lower -> _Mirror
_mirrors_lock = threading.Lock()
_loading_locks = {}


def _ttl() -> int:
    try:
        return int(os.getenv("DATASET_VAULT_CACHE_TTL_SECONDS", "900"))
    except ValueError:
        return 900


def _max_rows() -> int:
    try:
        return int(os.getenv("DATASET_VAULT_CACHE_MAX_ROWS", "3000000"))
    except ValueError:
        return 3_000_000


def invalidate(table_name: str):
    with _mirrors_lock:
        m = _mirrors.pop(_norm(table_name), None)
    if m:
        try:
            m.conn.close()
        except Exception:
            pass


def _evict_expired_locked():
    now = time.time()
    for k in [k for k, m in _mirrors.items() if now - m.loaded > _ttl()]:
        try:
            _mirrors.pop(k).conn.close()
        except Exception:
            pass
    while _mirrors and sum(m.rows for m in _mirrors.values()) > _max_rows():
        _, m = _mirrors.popitem(last=False)
        try:
            m.conn.close()
        except Exception:
            pass


def load_dataframe(table_name: str) -> pd.DataFrame:
    """Decrypt every chunk of a vaulted dataset (memory only)."""
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT WrappedKey FROM {META_TABLE} WHERE LOWER(TableName) = ?", (_norm(table_name),))
        row = cur.fetchone()
        if not row:
            raise RuntimeError("dataset not in vault")
        dk = _unwrap(row[0])
        cur.execute(f"SELECT ChunkNo, Payload FROM {VAULT_TABLE} WHERE LOWER(TableName) = ? ORDER BY ChunkNo", (_norm(table_name),))
        parts = [_decrypt_chunk(dk, table_name, int(n), p) for n, p in cur.fetchall()]
    finally:
        conn.close()
    if parts:
        return pd.concat(parts, ignore_index=True)
    cols = _shell_columns(table_name)
    return pd.DataFrame(columns=cols)


def _sqlite_ready(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            s = out[c]
            has_time = bool(((s.dt.hour != 0) | (s.dt.minute != 0) | (s.dt.second != 0)).fillna(False).any())
            out[c] = s.dt.strftime("%Y-%m-%d %H:%M:%S" if has_time else "%Y-%m-%d").where(s.notna(), None)
        elif pd.api.types.is_bool_dtype(out[c]):
            out[c] = out[c].astype("Int64")
    return out


def _get_mirror(table_name: str) -> _Mirror:
    key = _norm(table_name)
    with _mirrors_lock:
        _evict_expired_locked()
        m = _mirrors.get(key)
        if m:
            _mirrors.move_to_end(key)
            return m
        lk = _loading_locks.setdefault(key, threading.Lock())
    with lk:
        with _mirrors_lock:
            m = _mirrors.get(key)
            if m:
                return m
        df = load_dataframe(table_name)
        mem = sqlite3.connect(":memory:", check_same_thread=False)
        _register_sqlite_functions(mem)
        df = _sqlite_ready(df)
        shell_name = None
        for c in _shell_table_name_variants(table_name):
            shell_name = c
            break
        df.to_sql(shell_name, mem, index=False, chunksize=50_000)
        m = _Mirror(mem, len(df))
        with _mirrors_lock:
            _mirrors[key] = m
            _evict_expired_locked()
        return m


def _shell_table_name_variants(table_name: str):
    yield str(table_name).strip().strip("[]")


def _register_sqlite_functions(mem):
    """Functions the T-SQL translation relies on (same as the QA/dev SQLite adapter)."""
    try:
        from database.connection import SQLiteConnectionAdapter  # noqa: F401
        from database import connection as dbc
        reg = getattr(dbc, "register_sqlite_functions", None)
        if reg:
            reg(mem)
    except Exception:
        pass


def query(table_name: str, sql: str, params=None) -> pd.DataFrame:
    """Run an already-translated SQLite query against the decrypted in-memory copy."""
    m = _get_mirror(table_name)
    with m.lock:
        return pd.read_sql(sql, m.conn, params=params)


def prewarm(table_name: str):
    """Load the decrypted copy in the background right after an upload."""
    def _go():
        try:
            _get_mirror(table_name)
        except Exception as e:
            log.warning("Vault prewarm failed: %s", type(e).__name__)
    threading.Thread(target=_go, daemon=True).start()


# --------------------------------------------------------------------------- routing helper

def vaulted_table_in(sql: str):
    """Return the vaulted dataset table a SELECT reads, or None."""
    import re
    if not sql or not re.match(r"^\s*(?:WITH\b|SELECT\b|\()", sql, re.IGNORECASE):
        return None
    for name in re.findall(r"\b(?:FROM|JOIN)\s+\[?([A-Za-z0-9_]+)\]?", sql, flags=re.IGNORECASE):
        if name.lower() in (VAULT_TABLE.lower(), META_TABLE.lower()):
            continue
        if is_vaulted(name):
            return name
    return None


# --------------------------------------------------------------------------- migration

def migrate_table(table_name: str) -> int:
    """Move an existing plaintext dataset table into the vault and empty the plaintext table."""
    from database.connection import get_connection, sanitize_identifier
    if is_vaulted(table_name):
        return 0
    conn = get_connection()
    try:
        if _is_sqlite(conn):
            df = pd.read_sql(f"SELECT * FROM {sanitize_identifier(table_name)}", conn.conn)
        else:
            df = pd.read_sql(f"SELECT * FROM {sanitize_identifier(table_name)}", conn)
    finally:
        conn.close()
    try:
        from utils.encryption import decrypt_dataframe
        df = decrypt_dataframe(df)  # legacy per-cell "enc:" values
    except Exception:
        pass
    if "RecordID" in df.columns:
        df = df.sort_values("RecordID").drop(columns=["RecordID"])
    register_table(table_name)
    written = append_rows(table_name, df)
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {sanitize_identifier(table_name)}")
        conn.commit()
    finally:
        conn.close()
    return written


def migrate_all() -> dict:
    """Vault every dataset listed in Datasets. Safe to re-run (already-vaulted tables are skipped)."""
    from database.connection import get_db_cursor, is_safe_identifier
    with get_db_cursor() as cur:
        cur.execute("SELECT DatasetName FROM Datasets")
        names = [r[0] for r in cur.fetchall()]
    done, skipped, failed = 0, 0, []
    for n in names:
        if not is_safe_identifier(n or ""):
            continue
        try:
            if is_vaulted(n):
                skipped += 1
                continue
            migrate_table(n)
            done += 1
        except Exception as e:
            failed.append((n, type(e).__name__))
    return {"migrated": done, "already_encrypted": skipped, "failed": failed}


if __name__ == "__main__":  # python -m utils.dataset_vault migrate
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        if not master_key_ok():
            print("DATASET_ENCRYPTION_KEY is missing or invalid. Nothing was changed.")
            sys.exit(2)
        res = migrate_all()
        print(f"migrated={res['migrated']} already_encrypted={res['already_encrypted']} failed={len(res['failed'])}")
        for n, err in res["failed"]:
            print(f"  failed: {n} ({err})")
    else:
        print("usage: python -m utils.dataset_vault migrate")
