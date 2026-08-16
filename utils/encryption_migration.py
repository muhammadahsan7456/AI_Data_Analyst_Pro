import os
import sys
import math
import pandas as pd

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.encryption import encrypt_dataframe, _get_cipher


def migrate_and_encrypt_existing_tables():
    """
    Scans all existing dataset tables in SQL Server/SQLite, detects unencrypted string columns,
    and retroactively encrypts all existing rows with AES-256 Fernet Encryption at rest.
    """
    from database.connection import get_connection, get_db_cursor, sanitize_identifier

    cipher = _get_cipher()
    if not cipher:
        print("[ENCRYPTION MIGRATION] Encryption cipher not available. Skipping.")
        return 0

    table_names = []
    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT DISTINCT DatasetName FROM Datasets")
            rows = cursor.fetchall()
            table_names = [r[0] for r in rows if r and r[0]]
    except Exception as e:
        print("[ENCRYPTION MIGRATION] Error querying Datasets metadata table:", e)

    if not table_names:
        print("[ENCRYPTION MIGRATION] No existing dataset tables found in metadata.")
        return 0

    conn = get_connection()
    if not conn:
        print("[ENCRYPTION MIGRATION] Database connection failed.")
        return 0

    encrypted_count = 0
    try:
        cursor = conn.cursor()
        for tbl in table_names:
            safe_table = sanitize_identifier(tbl)
            try:
                # Read all rows from existing dataset table
                query = f"SELECT * FROM {safe_table}"
                df = pd.read_sql(query, conn)
                if df is None or df.empty:
                    continue

                # Check if table contains any unencrypted text string columns
                has_unencrypted = False
                for col in df.columns:
                    c_lower = str(col).lower().strip()
                    if c_lower in ["recordid", "s.no"]:
                        continue
                    if pd.api.types.is_string_dtype(df[col]) or str(df[col].dtype) in ["object", "string", "category", "str"]:
                        non_null_samples = df[col].dropna().astype(str).str.strip()
                        non_null_samples = non_null_samples[non_null_samples != ""]
                        if not non_null_samples.empty:
                            # If sample value does not start with 'enc:', it requires encryption!
                            if not non_null_samples.iloc[0].startswith("enc:"):
                                has_unencrypted = True
                                break

                if not has_unencrypted:
                    continue

                print(f"[ENCRYPTION MIGRATION] Encrypting existing plain-text table '{tbl}' ({len(df)} rows)...")

                # Remove RecordID if auto-identity column in SQL Server
                df_to_enc = df.copy()
                has_record_id = "RecordID" in df_to_enc.columns
                if has_record_id:
                    df_to_enc = df_to_enc.drop(columns=["RecordID"])

                # Encrypt text columns
                df_encrypted = encrypt_dataframe(df_to_enc)

                # Clear and re-populate table with encrypted data
                cursor.execute(f"DELETE FROM {safe_table}")
                conn.commit()

                columns = ", ".join(sanitize_identifier(col) for col in df_encrypted.columns)
                placeholders = ", ".join("?" for _ in df_encrypted.columns)
                insert_sql = f"INSERT INTO {safe_table} ({columns}) VALUES ({placeholders})"

                rows_data = []
                for _, row in df_encrypted.iterrows():
                    tuple_row = []
                    for val in row:
                        if val is None or (isinstance(val, float) and math.isnan(val)):
                            tuple_row.append(None)
                        elif isinstance(val, bool):
                            tuple_row.append(bool(val))
                        elif isinstance(val, (int, float)):
                            tuple_row.append(val)
                        else:
                            tuple_row.append(str(val))
                    rows_data.append(tuple(tuple_row))

                batch_size = 2000
                for i in range(0, len(rows_data), batch_size):
                    batch = rows_data[i:i + batch_size]
                    cursor.executemany(insert_sql, batch)
                    conn.commit()

                encrypted_count += 1
                print(f"[ENCRYPTION MIGRATION] Table '{tbl}' successfully encrypted at rest!")
            except Exception as tbl_err:
                print(f"[ENCRYPTION MIGRATION] Error encrypting table '{tbl}':", tbl_err)
    finally:
        conn.close()

    print(f"[ENCRYPTION MIGRATION] Completed. Total tables retroactively encrypted: {encrypted_count}")
    return encrypted_count


if __name__ == "__main__":
    migrate_and_encrypt_existing_tables()
