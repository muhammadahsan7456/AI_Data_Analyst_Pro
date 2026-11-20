"""
R-Phase 9.3: the Super Admin console never displays, returns or exports dataset row
contents - only metadata. A dataset with unique marker values is stored through the real
encrypted vault, then every admin page and export is fetched as a signed-in Super Admin
and searched for those markers.
"""
import io
import json
import re
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from cryptography.fernet import Fernet
from flask import Flask

import admin.routes as admin_routes
from admin import admin_bp
from auth import auth_bp
from database import connection as db
from frontend.routes import frontend
from utils import dataset_vault as vault

MARK_TEXT = "ZQX-PRIVATE-ROW-7731"
MARK_NUM = "987654.321"


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    db_file = str(tmp_path / "admin_privacy.db")
    monkeypatch.setenv("DATASET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(db, "get_connection", lambda: db.SQLiteConnectionAdapter(db_file))
    import uploads.data_loader as dl
    monkeypatch.setattr(dl, "get_connection", lambda: db.SQLiteConnectionAdapter(db_file))
    monkeypatch.setattr(vault, "_schema_ready", False)
    monkeypatch.setattr(vault, "_meta_cache", {})
    vault._mirrors.clear()

    conn = db.SQLiteConnectionAdapter(db_file)
    db.init_sqlite_db(conn.conn)
    cur = conn.conn.cursor()
    cur.execute("INSERT INTO Users (UserID, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role) "
                "VALUES (99, 'root', 'Root Admin', 'root@example.com', 'x', 1, 1, 'SuperAdmin')")
    cur.execute("INSERT INTO Users (UserID, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role, SubscriptionStatus) "
                "VALUES (5, 'owner', 'Data Owner', 'owner@example.com', 'x', 1, 1, 'Analyst', 'free')")
    cur.execute("INSERT INTO Datasets (UserID, DatasetName, OriginalFileName, FileType, TotalRows, TotalColumns, StorageSizeKB) "
                "VALUES (5, 'ds_5_private', 'private.csv', 'csv', 3, 3, 1.2)")
    conn.conn.commit()
    conn.close()

    from uploads.data_loader import create_table, insert_dataframe
    df = pd.DataFrame({"Customer": [MARK_TEXT, "Other", "Third"], "Amount": [float(MARK_NUM), 1.0, 2.0], "City": ["Lahore"] * 3})
    create_table("ds_5_private", df.copy())
    insert_dataframe("ds_5_private", df)

    captured = []

    def fake_render(name, **ctx):
        blob = json.dumps(ctx, default=str)
        captured.append((name, blob))
        return blob

    monkeypatch.setattr(admin_routes, "render_template", fake_render)
    monkeypatch.setattr(admin_routes, "log_audit_event", lambda *a, **k: None)

    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(frontend)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_user_id"] = 99
        s["admin_user_role"] = "SuperAdmin"
    return client, captured, db_file


def _texts(resp):
    data = resp.data
    out = [data.decode("latin-1")]
    if data[:2] == b"PK":  # xlsx/docx: look inside the zip
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            out += [z.read(n).decode("latin-1") for n in z.namelist()]
    return "\n".join(out)


def test_marker_rows_are_really_stored_encrypted(admin_client):
    _, _, db_file = admin_client
    import sqlite3
    raw = sqlite3.connect(db_file)
    assert raw.execute("SELECT COUNT(*) FROM ds_5_private").fetchone()[0] == 0
    blob = b"".join(bytes(r[0]) for r in raw.execute("SELECT Payload FROM DatasetVault"))
    assert MARK_TEXT.encode() not in blob


def test_no_admin_page_or_export_contains_row_values(admin_client):
    client, captured, _ = admin_client
    pages = ["/admin/dashboard", "/admin/users", "/admin/datasets", "/admin/datasets?q=private",
             "/admin/payments", "/admin/audit-logs", "/admin/settings", "/admin/subscribers",
             "/admin/contact-messages", "/admin/reviews",
             "/admin/users/export/csv", "/admin/users/export/excel", "/admin/payments/export/csv",
             "/admin/payments/export/excel"]
    checked = 0
    for path in pages:
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code)
        text = _texts(r)
        assert MARK_TEXT not in text, f"row content leaked on {path}"
        assert MARK_NUM not in text, f"row value leaked on {path}"
        checked += 1
    # The dataset page does show the metadata.
    ds = [blob for name, blob in captured if name == "admin/datasets.html"]
    assert ds and "private.csv" in ds[0] and "owner@example.com" in ds[0]
    assert checked == len(pages)


def test_admin_code_never_reads_dataset_rows():
    src = Path(admin_routes.__file__).read_text()
    for forbidden in ("run_query(", "dataset_vault.query", "load_dataframe", "vault.query(", "SELECT * FROM {"):
        assert forbidden not in src, forbidden
    # The only vault call in the admin console is drop() on user deletion.
    assert set(re.findall(r"dataset_vault\.(\w+)\(", src)) <= {"drop"}
