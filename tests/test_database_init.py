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
