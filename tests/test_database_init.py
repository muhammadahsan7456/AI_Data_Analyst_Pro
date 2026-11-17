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
