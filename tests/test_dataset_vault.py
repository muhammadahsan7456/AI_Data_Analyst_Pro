"""Encrypted dataset storage: rows never stored in plaintext, queries still answer correctly."""
import sqlite3

import pandas as pd
import pytest
from cryptography.fernet import Fernet

from database import connection as db
from utils import dataset_vault as vault


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_file = str(tmp_path / "vault.db")
    monkeypatch.setenv("DATASET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(db, "get_connection", lambda: db.SQLiteConnectionAdapter(db_file))
    import uploads.data_loader as dl
    monkeypatch.setattr(dl, "get_connection", lambda: db.SQLiteConnectionAdapter(db_file))
    monkeypatch.setattr(vault, "_schema_ready", False)
    monkeypatch.setattr(vault, "_meta_cache", {})
    vault._mirrors.clear()
    try:
        from utils.cache import system_cache
        if system_cache:
            system_cache.clear()
    except Exception:
        pass
    return db_file


def _make(n=500):
    return pd.DataFrame({
        "City": (["Lahore", "Karachi", "Quetta"] * n)[:n],
        "Status": (["Delivered", "Cancelled"] * n)[:n],
        "Sales": [round(1234567.89 + i * 0.01, 2) for i in range(n)],
        "Qty": list(range(n)),
    })


def _create(table, df):
    from uploads.data_loader import create_table, insert_dataframe
    create_table(table, df.copy())
    return insert_dataframe(table, df)


def test_rows_are_only_stored_encrypted(env):
    df = _make()
    res = _create("t_u1", df)
    assert res["inserted"] == len(df)
    raw = sqlite3.connect(env)
    assert raw.execute("SELECT COUNT(*) FROM t_u1").fetchone()[0] == 0
    payloads = b"".join(bytes(r[0]) for r in raw.execute("SELECT Payload FROM DatasetVault"))
    assert b"Lahore" not in payloads and b"Cancelled" not in payloads
    assert raw.execute("SELECT TotalRows FROM DatasetVaultMeta").fetchone()[0] == len(df)


def test_queries_answer_from_encrypted_rows(env):
    df = _make()
    _create("t_u1", df)
    got = db.run_query("SELECT COUNT(*) AS n FROM [t_u1] WHERE [Status] = ?", ("Cancelled",))
    assert int(got.iloc[0, 0]) == (df.Status == "Cancelled").sum()
    got = db.run_query("SELECT TOP (1) SUM(TRY_CAST(REPLACE(CAST([Sales] AS NVARCHAR(MAX)), ',', '') AS FLOAT)) AS s, [City] "
                       "FROM [t_u1] GROUP BY [City] ORDER BY s DESC;")
    exp = df.groupby("City").Sales.sum().sort_values(ascending=False)
    assert got.iloc[0]["City"] == exp.index[0]
    assert round(float(got.iloc[0]["s"]), 2) == round(float(exp.iloc[0]), 2)


def test_float_values_keep_full_precision(env):
    df = _make(10)
    _create("t_u1", df)
    back = vault.load_dataframe("t_u1")
    assert back["Sales"].tolist() == df["Sales"].tolist()


def test_missing_key_refuses_upload_instead_of_plaintext(env, monkeypatch):
    monkeypatch.delenv("DATASET_ENCRYPTION_KEY", raising=False)
    with pytest.raises(vault.VaultUnavailable):
        _create("t_u2", _make(5))
    raw = sqlite3.connect(env)
    assert raw.execute("SELECT name FROM sqlite_master WHERE name='t_u2'").fetchone() is None


def test_wrong_key_cannot_read(env, monkeypatch):
    _create("t_u1", _make(5))
    vault._mirrors.clear()
    monkeypatch.setenv("DATASET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(Exception):
        vault.load_dataframe("t_u1")


def test_migration_moves_plaintext_into_vault(env, monkeypatch):
    monkeypatch.setenv("DATASET_VAULT", "off")
    df = _make(50)
    _create("old_u1", df)
    raw = sqlite3.connect(env)
    assert raw.execute("SELECT COUNT(*) FROM old_u1").fetchone()[0] == 50
    monkeypatch.setenv("DATASET_VAULT", "on")
    assert vault.migrate_table("old_u1") == 50
    assert raw.execute("SELECT COUNT(*) FROM old_u1").fetchone()[0] == 0
    assert vault.migrate_table("old_u1") == 0  # idempotent
    assert len(db.run_query("SELECT * FROM [old_u1]")) == 50


def test_drop_removes_ciphertext(env):
    _create("t_u1", _make(5))
    vault.drop("t_u1")
    raw = sqlite3.connect(env)
    assert raw.execute("SELECT COUNT(*) FROM DatasetVault").fetchone()[0] == 0
    assert not vault.is_vaulted("t_u1")
