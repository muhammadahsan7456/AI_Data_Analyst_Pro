import sqlite3

import pytest

import database.connection as connection


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
