"""
R-Phase 9.3: Pro lasts 30 days, then the account returns to the Free plan automatically.
Runs the real scheduler sweep on a temporary SQLite database (the old sweep used SQL
Server-only UPDATE ... OUTPUT and failed on SQLite with 'near ".": syntax error').
"""
import pytest

import utils.scheduler as scheduler
import utils.subscription as subscription
from database import connection as db
from database.queries import AUTO_EXPIRY_REASON


@pytest.fixture
def qdb(tmp_path, monkeypatch):
    db_file = str(tmp_path / "expiry.db")
    monkeypatch.setattr(db, "get_connection", lambda: db.SQLiteConnectionAdapter(db_file))
    conn = db.SQLiteConnectionAdapter(db_file)
    db.init_sqlite_db(conn.conn)
    cur = conn.conn.cursor()
    rows = [
        # id, status, end (relative days), IsActive, role, reason
        (1, "active", -1, 1, "Analyst", None),           # lapsed yesterday -> free + email
        (2, "expiring_soon", -3, 1, "Analyst", None),    # lapsed -> free + email
        (3, "active", 10, 1, "Analyst", None),           # still paid -> untouched
        (4, "suspended", -40, 1, "Analyst", AUTO_EXPIRY_REASON),  # old sweep -> free, no email
        (5, "suspended", -5, 0, "Analyst", "Abuse"),      # admin ban -> untouched
        (6, "active", -2, 1, "SuperAdmin", None),         # admin -> untouched
        (7, "expired", -6, 1, "Analyst", None),           # marked expired by page view -> free + email
    ]
    for uid, st, days, act, role, reason in rows:
        cur.execute(
            "INSERT INTO Users (UserID, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role, "
            "SubscriptionStatus, SubscriptionEndDate, SuspensionReason) VALUES (?,?,?,?, 'x', ?, 1, ?, ?, datetime('now', ?), ?)",
            (uid, f"u{uid}", f"User {uid}", f"u{uid}@example.com", act, role, st, f"{days} days", reason),
        )
    conn.conn.commit()
    conn.close()

    sent = []
    monkeypatch.setattr(scheduler.email_service, "send_subscription_suspended_email",
                        lambda email, name: sent.append(email) or True)
    monkeypatch.setattr(scheduler.email_service, "send_subscription_reminder_email", lambda *a, **k: True)

    def status():
        with db.get_db_cursor() as c:
            c.execute("SELECT UserID, SubscriptionStatus FROM Users ORDER BY UserID")
            return dict(c.fetchall())
    return status, sent


def test_sweep_returns_lapsed_pro_users_to_free(qdb, capsys):
    status, sent = qdb
    scheduler.process_subscription_expiry_sweep()
    st = status()
    assert st == {1: "free", 2: "free", 3: "active", 4: "free", 5: "suspended", 6: "active", 7: "free"}
    assert sorted(sent) == ["u1@example.com", "u2@example.com", "u7@example.com"]
    assert "syntax error" not in capsys.readouterr().out


def test_sweep_is_idempotent(qdb):
    status, sent = qdb
    scheduler.process_subscription_expiry_sweep()
    first = (status(), list(sent))
    scheduler.process_subscription_expiry_sweep()
    assert (status(), sent) == first


def test_lapsed_user_gets_free_limits_and_no_suspended_banner(qdb):
    info = subscription.get_user_subscription_info(4)  # legacy 'suspended' row, still active
    assert info["is_pro"] is False and info["max_upload_mb"] == 1
    assert "SUSPENDED" not in (info.get("banner_message") or "").upper()
    banned = subscription.get_user_subscription_info(5)
    assert banned["status"] == "suspended"
