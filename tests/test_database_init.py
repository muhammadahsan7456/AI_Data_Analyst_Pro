from datetime import datetime
import sqlite3

import pytest

import database.connection as connection
import database.queries as queries


def test_failed_db_connection_does_not_mark_initialization_complete(monkeypatch):
    monkeypatch.setattr(connection, "_db_initialized", False)

    def fail_connection():
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(connection, "get_connection", fail_connection)

    with pytest.raises(ConnectionError, match="database unavailable"):
        connection.init_db()

    assert connection._db_initialized is False


def test_sqlite_init_adds_subscription_columns_to_existing_users(tmp_path):
    db_path = tmp_path / "existing.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE Users (UserID INTEGER PRIMARY KEY, PasswordHash TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO Users (UserID, PasswordHash) VALUES (1, 'existing-hash')"
    )

    connection.init_sqlite_db(conn)

    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(Users)").fetchall()
    }
    user = conn.execute(
        "SELECT SubscriptionStatus, SubscriptionEndDate, SuspendedAt, SuspensionReason "
        "FROM Users WHERE UserID = 1"
    ).fetchone()
    conn.close()

    assert {
        "SubscriptionStatus",
        "SubscriptionEndDate",
        "SuspendedAt",
        "SuspensionReason",
    }.issubset(columns)
    assert user == ("free", None, None, None)


def test_existing_base_table_discovery_supports_sqlite(monkeypatch, tmp_path):
    db_path = tmp_path / "tables.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE qa_sales (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        connection,
        "get_connection",
        lambda: connection.SQLiteConnectionAdapter(str(db_path)),
    )

    tables = connection.get_existing_base_tables(use_cache=False)

    assert "qa_sales" in tables
    assert "sqlite_sequence" not in tables


def test_sqlite_init_adds_payment_review_columns_and_site_announcement(tmp_path):
    db_path = tmp_path / "legacy-payments.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE Payments (PaymentID INTEGER PRIMARY KEY, UserID INTEGER, Amount REAL, "
        "Currency TEXT, PaymentMethod TEXT, TransactionID TEXT, Status TEXT, PlanName TEXT, "
        "PaymentDate TEXT)"
    )
    conn.execute(
        "INSERT INTO Payments (PaymentID, UserID, Amount, TransactionID, Status) "
        "VALUES (1, 7, 1499, 'legacy-txn', 'Pending')"
    )

    connection.init_sqlite_db(conn)

    payment_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(Payments)").fetchall()
    }
    payment = conn.execute(
        "SELECT PaymentType, RejectionReason, ReviewedBy, ReviewedAt "
        "FROM Payments WHERE PaymentID = 1"
    ).fetchone()
    announcement_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'SiteAnnouncement'"
    ).fetchone()
    payment_settings = conn.execute(
        "SELECT PlanName, PlanPriceLabel, AccountTitle FROM PaymentSettings"
    ).fetchone()
    conn.close()

    assert {
        "ScreenshotPath",
        "PaymentType",
        "RejectionReason",
        "ReviewedBy",
        "ReviewedAt",
    }.issubset(payment_columns)
    assert payment == ("New", None, None, None)
    assert announcement_exists == (1,)
    assert payment_settings == ("Pro Plan", "PKR 1,499 / month", "")


def test_sqlite_adapter_translates_offset_fetch_pagination(tmp_path):
    db_path = tmp_path / "pagination.sqlite"
    conn = connection.SQLiteConnectionAdapter(str(db_path))
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE qa_rows (id INTEGER PRIMARY KEY)")
    cursor.executemany("INSERT INTO qa_rows (id) VALUES (?)", [(i,) for i in range(1, 6)])

    cursor.execute(
        "SELECT id FROM qa_rows ORDER BY id OFFSET 1 ROWS FETCH NEXT 2 ROWS ONLY"
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    assert rows == [(2,), (3,)]


def test_sqlite_adapter_dateadd_clamps_month_end(tmp_path):
    conn = connection.SQLiteConnectionAdapter(str(tmp_path / "dateadd.sqlite"))
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE qa_dates (value TEXT)")
    cursor.execute("INSERT INTO qa_dates (value) VALUES (?)", ("2024-01-31",))
    cursor.execute(
        "SELECT DATEADD(month, 1, CAST(value AS DATE)) FROM qa_dates"
    )
    result = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    assert result == "2024-02-29"


def test_sqlite_adapter_handles_nested_top_for_admin_users(tmp_path):
    conn = connection.SQLiteConnectionAdapter(str(tmp_path / "admin-users.sqlite"))
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE Users (UserID INTEGER, FirstName TEXT, LastName TEXT, Username TEXT, "
        "FullName TEXT, Email TEXT, PhoneNumber TEXT, Country TEXT, City TEXT, ProfileImage TEXT, "
        "IsActive INTEGER, IsVerified INTEGER, Role TEXT, CreatedAt TEXT, SubscriptionStatus TEXT, "
        "SubscriptionEndDate TEXT)"
    )
    cursor.execute(
        "CREATE TABLE AuditLogs (UserID INTEGER, IPAddress TEXT, CreatedAt TEXT)"
    )
    cursor.execute(
        "INSERT INTO Users (UserID, FullName, Email, IsActive, IsVerified, Role, CreatedAt, "
        "SubscriptionStatus) VALUES (1, 'QA Admin View', 'qa@example.invalid', 1, 1, "
        "'Analyst', '2024-01-15', 'free')"
    )
    cursor.execute(
        "INSERT INTO AuditLogs (UserID, IPAddress, CreatedAt) VALUES (1, '192.0.2.1', '2024-01-15')"
    )

    cursor.execute(queries.get_all_users_admin(limit=5))
    recent_rows = cursor.fetchall()
    cursor.execute(queries.search_users_admin(page=1, per_page=5))
    paginated_rows = cursor.fetchall()
    cursor.execute(queries.count_users_admin())
    total_users = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    assert len(recent_rows) == 1
    assert recent_rows[0][14] == "192.0.2.1"
    assert len(paginated_rows) == 1
    assert paginated_rows[0][14] == "192.0.2.1"
    assert total_users == 1


def test_sqlite_adapter_supports_admin_dashboard_monthly_trends(tmp_path):
    conn = connection.SQLiteConnectionAdapter(str(tmp_path / "admin-trends.sqlite"))
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE Payments (Status TEXT, PaymentDate TEXT, Amount REAL)")
    cursor.execute("INSERT INTO Payments VALUES ('Completed', '2024-01-15 12:00:00', 1499)")
    cursor.execute("CREATE TABLE Users (CreatedAt TEXT)")
    cursor.execute("INSERT INTO Users VALUES ('2024-01-15 12:00:00')")
    start = datetime(2024, 1, 1)
    end = datetime(2024, 1, 31)

    cursor.execute(queries.get_monthly_revenue_trend(), (start, end))
    revenue = cursor.fetchall()
    cursor.execute(queries.get_monthly_signups_trend(), (start, end))
    signups = cursor.fetchall()
    cursor.close()
    conn.close()

    assert revenue == [("2024-01", 1499.0)]
    assert signups == [("2024-01", 1)]


def test_sqlite_user_profile_queries_insert_and_update(tmp_path):
    conn = connection.SQLiteConnectionAdapter(str(tmp_path / "profile.sqlite"))
    connection.init_sqlite_db(conn)
    cursor = conn.cursor()
    initial_values = (
        9,
        "Initial bio",
        "Analyst",
        "Initial Company",
        "Data",
        "Analyst",
        "",
        "",
        "",
        "",
        "UTC",
        "en",
    )
    cursor.execute(queries.insert_user_profile_details(), initial_values)
    cursor.execute(
        queries.update_user_profile_details(),
        (
            "Updated bio",
            "Senior Analyst",
            "Updated Company",
            "Research",
            "Lead",
            "",
            "",
            "",
            "",
            "UTC",
            "en",
            9,
        ),
    )
    cursor.execute(
        "SELECT Bio, Occupation, Company FROM UserProfiles WHERE UserID = ?",
        (9,),
    )
    profile = cursor.fetchone()
    cursor.close()
    conn.close()

    assert profile == ("Updated bio", "Senior Analyst", "Updated Company")


def test_sqlite_adds_dataset_insight_columns_for_scheduler(tmp_path):
    """R-Phase 8: the insights scan failed with 'no such column: D.InsightsEnabled' on SQLite."""
    db_file = str(tmp_path / "old.db")
    raw = sqlite3.connect(db_file)
    raw.execute(
        "CREATE TABLE Datasets (DatasetID INTEGER PRIMARY KEY AUTOINCREMENT, UserID INTEGER NOT NULL, "
        "DatasetName TEXT NOT NULL, OriginalFileName TEXT NOT NULL, FileType TEXT NOT NULL, "
        "TotalRows INTEGER DEFAULT 0, TotalColumns INTEGER DEFAULT 0, StorageSizeKB REAL DEFAULT 0.0, "
        "IsFavorite INTEGER DEFAULT 0, Tags TEXT, LastOpenedAt TEXT, "
        "UploadDate TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    raw.execute("INSERT INTO Datasets (UserID, DatasetName, OriginalFileName, FileType) VALUES (1, 'd', 'd.csv', 'csv')")
    raw.commit()
    raw.close()

    conn = connection.SQLiteConnectionAdapter(db_file)
    connection.init_sqlite_db(conn)
    cur = conn.cursor()
    cur.execute(queries.get_datasets_due_for_insight_scan())
    assert [r[0] for r in cur.fetchall()] == [1]
    cur.execute(queries.mark_dataset_insight_scanned(), (1,))
    conn.commit()
    cur.execute(queries.get_datasets_due_for_insight_scan())
    assert cur.fetchall() == []
    conn.close()


def test_sqlite_schema_has_every_column_sql_server_init_adds(tmp_path):
    import re
    from pathlib import Path
    src = Path(connection.__file__).read_text()
    pairs = set(re.findall(r"OBJECT_ID\('(\w+)'\) AND name = '(\w+)'", src))
    conn = connection.SQLiteConnectionAdapter(str(tmp_path / "fresh.db"))
    connection.init_sqlite_db(conn)
    cur = conn.cursor()
    missing = []
    for table, column in sorted(pairs):
        cur.execute(f"PRAGMA table_info({table})")
        if column not in {r[1] for r in cur.fetchall()}:
            missing.append((table, column))
    conn.close()
    assert pairs and missing == []
