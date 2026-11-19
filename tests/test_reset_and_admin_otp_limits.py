"""
R-Phase 8 security fixes, exercised against a real (temporary) SQLite database built with
the app's own schema, so the counters are checked where they actually live.

A) Password reset codes: looked up by email + code, stored as a keyed HMAC digest,
   5 wrong codes invalidate the code, counter in PasswordResetTokens.Attempts.
B) Super Admin email OTP: counter and 15-minute lockout on the Users row; replaying an
   older session cookie cannot reset either; success clears them.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from flask import Flask
from http.cookies import SimpleCookie

import admin.routes as admin_routes
import auth.routes as auth_routes
from admin import admin_bp
from auth import auth_bp
from auth.email_service import EmailService
from auth.security import otp_digest
from database.connection import SQLiteConnectionAdapter, init_sqlite_db

SECRET = "test-secret-key"
USER_EMAIL = "reset-user@example.com"
ADMIN_EMAIL = "root-admin@example.com"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "qa.db")
    conn = SQLiteConnectionAdapter(path)
    init_sqlite_db(conn.conn)
    cur = conn.conn.cursor()
    cur.execute(
        "INSERT INTO Users (UserID, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role) "
        "VALUES (11, 'resetuser', 'Reset User', ?, 'old-hash', 1, 1, 'Analyst')",
        (USER_EMAIL,),
    )
    cur.execute(
        "INSERT INTO Users (UserID, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role) "
        "VALUES (12, 'otheruser', 'Other User', 'other@example.com', 'other-hash', 1, 1, 'Analyst')"
    )
    cur.execute(
        "INSERT INTO Users (UserID, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role) "
        "VALUES (99, 'rootadmin', 'Root Admin', ?, 'admin-hash', 1, 1, 'SuperAdmin')",
        (ADMIN_EMAIL,),
    )
    conn.conn.commit()
    conn.close()

    @contextmanager
    def cursor(commit=False):
        c = SQLiteConnectionAdapter(path)
        cur = c.cursor()
        try:
            yield cur
            if commit:
                c.commit()
        finally:
            c.close()

    for mod in (auth_routes, admin_routes):
        monkeypatch.setattr(mod, "get_db_cursor", cursor)
        monkeypatch.setattr(mod, "render_template", lambda *a, **k: "page")
        monkeypatch.setattr(mod, "log_audit_event", lambda *a, **k: None)
        monkeypatch.setattr(mod, "log_login_attempt", lambda *a, **k: None)
    monkeypatch.setattr(auth_routes, "trigger_ai_event", lambda *a, **k: None)
    monkeypatch.setattr(admin_routes, "verify_password", lambda pw, h: pw == "Correct#Pass1")

    def query(sql, params=()):
        with cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    return query


def _app():
    app = Flask(__name__)
    app.secret_key = SECRET
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    return app


def _flashes(client):
    with client.session_transaction() as s:
        return [m for _, m in s.get("_flashes", [])]


def _session_cookie(client):
    return client.get_cookie("session").value


def _set_session_cookie(client, value):
    client.set_cookie("session", value)


# ---------------------------------------------------------------- A) password reset

@pytest.fixture
def reset_client(db, monkeypatch):
    mailed = []
    monkeypatch.setattr(
        auth_routes.email_service, "send_password_reset_email",
        lambda email, name, code, link=None: mailed.append((email, code)) or True,
    )
    client = _app().test_client()
    return client, mailed


def _request_code(client, mailed, email=USER_EMAIL):
    r = client.post("/auth/forgot-password", data={"email": email})
    assert r.status_code == 302
    return mailed[-1][1]


def _wrong(code):
    return "123456" if code != "123456" else "654321"


def _verify(client, email, code):
    return client.post("/auth/reset-password/verify-code", data={"email": email, "otp_code": code}).get_json()


def _reset(client, email, code, pw="NewPass#2026"):
    return client.post("/auth/reset-password/verify", data={
        "email": email, "otp_code": code, "password": pw, "confirm_password": pw,
    })


def test_reset_code_is_stored_as_keyed_digest_not_plaintext(reset_client, db):
    client, mailed = reset_client
    code = _request_code(client, mailed)
    rows = db("SELECT Token, Attempts, IsUsed FROM PasswordResetTokens WHERE UserID = 11")
    assert len(rows) == 1
    token, attempts, used = rows[0]
    assert token != code and code not in token
    with client.application.app_context():
        assert token == otp_digest("password-reset", 11, code)
    assert (attempts, used) == (0, 0)


def test_five_wrong_codes_invalidate_then_correct_code_rejected(reset_client, db):
    client, mailed = reset_client
    code = _request_code(client, mailed)

    for i in range(1, 6):
        body = _verify(client, USER_EMAIL, _wrong(code))
        assert body["valid"] is False
        attempts, used = db("SELECT Attempts, IsUsed FROM PasswordResetTokens WHERE UserID = 11")[0]
        assert attempts == i  # counter lives in the database
    assert used == 1  # 5th wrong code burned it

    assert _verify(client, USER_EMAIL, code)["valid"] is False
    r = _reset(client, USER_EMAIL, code)
    assert r.status_code == 200  # re-rendered with the error, no redirect to login
    assert db("SELECT PasswordHash FROM Users WHERE UserID = 11")[0][0] == "old-hash"


def test_wrong_attempts_across_both_steps_share_one_counter(reset_client, db):
    client, mailed = reset_client
    code = _request_code(client, mailed)
    for _ in range(3):
        _verify(client, USER_EMAIL, _wrong(code))
    for _ in range(2):
        _reset(client, USER_EMAIL, _wrong(code))
    assert db("SELECT Attempts, IsUsed FROM PasswordResetTokens WHERE UserID = 11")[0] == (5, 1)
    _reset(client, USER_EMAIL, code)
    assert db("SELECT PasswordHash FROM Users WHERE UserID = 11")[0][0] == "old-hash"


def test_right_code_with_wrong_email_fails(reset_client, db):
    client, mailed = reset_client
    code = _request_code(client, mailed)
    for email in ("other@example.com", "nobody@example.com"):
        assert _verify(client, email, code)["valid"] is False
        _reset(client, email, code)
    assert db("SELECT PasswordHash FROM Users WHERE UserID IN (11, 12) ORDER BY UserID") == [("old-hash",), ("other-hash",)]
    # Valid-but-wrong account did not burn the real account's code either.
    assert db("SELECT Attempts, IsUsed FROM PasswordResetTokens WHERE UserID = 11")[0] == (0, 0)


def test_generic_message_for_unknown_email_wrong_code_and_burned_code(reset_client):
    client, mailed = reset_client
    code = _request_code(client, mailed)
    unknown = _verify(client, "nobody@example.com", code)["message"]
    wrong = _verify(client, USER_EMAIL, _wrong(code))["message"]
    for _ in range(4):
        _verify(client, USER_EMAIL, _wrong(code))
    burned = _verify(client, USER_EMAIL, code)["message"]
    assert unknown == wrong == burned


def test_cookie_replay_does_not_reset_reset_counter(reset_client, db):
    client, mailed = reset_client
    code = _request_code(client, mailed)
    early_cookie = _session_cookie(client)
    for _ in range(5):
        _verify(client, USER_EMAIL, _wrong(code))
    _set_session_cookie(client, early_cookie)
    assert _verify(client, USER_EMAIL, code)["valid"] is False
    assert db("SELECT Attempts, IsUsed FROM PasswordResetTokens WHERE UserID = 11")[0] == (5, 1)


def test_correct_code_and_email_resets_password_once(reset_client, db):
    client, mailed = reset_client
    code = _request_code(client, mailed)
    _verify(client, USER_EMAIL, _wrong(code))
    assert _verify(client, USER_EMAIL, code)["valid"] is True
    r = _reset(client, USER_EMAIL, code)
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/login")
    assert db("SELECT PasswordHash FROM Users WHERE UserID = 11")[0][0] != "old-hash"
    assert db("SELECT IsUsed FROM PasswordResetTokens WHERE UserID = 11")[0][0] == 1
    # Single use.
    assert _verify(client, USER_EMAIL, code)["valid"] is False


def test_new_request_retires_older_code(reset_client, db):
    client, mailed = reset_client
    first = _request_code(client, mailed)
    second = _request_code(client, mailed)
    if first != second:
        assert _verify(client, USER_EMAIL, first)["valid"] is False
    assert _verify(client, USER_EMAIL, second)["valid"] is True
    assert db("SELECT COUNT(*) FROM PasswordResetTokens WHERE UserID = 11 AND IsUsed = 0")[0][0] == 1


# ---------------------------------------------------------------- B) admin OTP

class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 19, 12, 0, 0)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def admin_client(db, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(admin_routes, "_now", clock)
    mailed = []
    monkeypatch.setattr(
        EmailService, "send_admin_login_otp_email",
        lambda self, **kw: mailed.append(kw["otp_code"]) or True,
    )
    return _app().test_client(), mailed, clock


def _admin_login(client, mailed):
    r = client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": "Correct#Pass1"})
    return r, (mailed[-1] if mailed else None)


def _otp(client, code):
    return client.post("/admin/verify-otp", data={"otp_code": code})


def _logged_in(client):
    with client.session_transaction() as s:
        return s.get("admin_user_id") == 99


def _state(db):
    return db("SELECT AdminOtpFailedAttempts, AdminOtpLockoutUntil, AdminOtpHash FROM Users WHERE UserID = 99")[0]


def test_admin_otp_cookie_holds_no_code_or_counter(admin_client, db):
    client, mailed, _ = admin_client
    r, code = _admin_login(client, mailed)
    assert r.headers["Location"].endswith("/admin/verify-otp")
    with client.session_transaction() as s:
        pending = s["pending_admin_otp"]
        assert "otp_hash" not in pending and "attempts" not in pending
        assert code not in str(dict(s))
    failed, locked, digest = _state(db)
    assert failed == 0 and locked is None
    with client.application.app_context():
        assert digest == otp_digest("admin-otp", 99, code)


def test_admin_otp_lockout_replay_and_recovery(admin_client, db):
    client, mailed, clock = admin_client
    _, code = _admin_login(client, mailed)
    early_cookie = _session_cookie(client)  # pending session before any wrong code

    for i in range(1, 5):
        _otp(client, _wrong(code))
        assert _state(db)[0] == i
        assert not _logged_in(client)
    r = _otp(client, _wrong(code))  # 5th wrong code
    assert r.headers["Location"].endswith("/admin/login")
    failed, locked, digest = _state(db)
    assert failed == 5 and locked is not None and digest is None
    assert "paused after 5 incorrect codes" in _flashes(client)[-1]
    assert "15 minutes" in _flashes(client)[-1]

    # Replaying the earlier cookie with the CORRECT code is still refused.
    _set_session_cookie(client, early_cookie)
    r = _otp(client, code)
    assert r.headers["Location"].endswith("/admin/login")
    assert not _logged_in(client)
    assert _state(db)[0] == 5

    # Correct password during the lockout does not issue a new code either.
    sent_before = len(mailed)
    clock.advance(minutes=14)
    r, _ = _admin_login(client, mailed)
    assert r.status_code == 200 and len(mailed) == sent_before
    assert "1 minute" in _flashes(client)[-1]

    # After 15 minutes: the old code stays dead, a fresh sign-in works.
    clock.advance(minutes=1, seconds=1)
    _set_session_cookie(client, early_cookie)
    _otp(client, code)
    assert not _logged_in(client)
    r, new_code = _admin_login(client, mailed)
    assert r.headers["Location"].endswith("/admin/verify-otp")
    assert _state(db)[0] == 0
    r = _otp(client, new_code)
    assert r.status_code == 302 and _logged_in(client)
    assert _state(db) == (0, None, None)


def test_admin_new_code_does_not_reset_failure_count(admin_client, db):
    client, mailed, _ = admin_client
    _, code = _admin_login(client, mailed)
    for _ in range(3):
        _otp(client, _wrong(code))
    _, code2 = _admin_login(client, mailed)
    assert _state(db)[0] == 3
    _otp(client, _wrong(code2))
    _otp(client, _wrong(code2))
    assert _state(db)[0] == 5 and _state(db)[1] is not None
    _otp(client, code2)
    assert not _logged_in(client)


def test_admin_success_clears_counter_and_code_is_single_use(admin_client, db):
    client, mailed, _ = admin_client
    _, code = _admin_login(client, mailed)
    pending_cookie = _session_cookie(client)
    _otp(client, _wrong(code))
    _otp(client, _wrong(code))
    assert _state(db)[0] == 2
    r = _otp(client, code)
    assert r.status_code == 302 and _logged_in(client)
    assert _state(db) == (0, None, None)

    # Replaying the pending cookie with the same (used) code does not log in again.
    client2 = client.application.test_client()
    _set_session_cookie(client2, pending_cookie)
    _otp(client2, code)
    assert not _logged_in(client2)


def test_admin_code_expires_after_ten_minutes(admin_client, db):
    client, mailed, clock = admin_client
    _, code = _admin_login(client, mailed)
    clock.advance(minutes=10, seconds=1)
    r = _otp(client, code)
    assert r.headers["Location"].endswith("/admin/login")
    assert not _logged_in(client)
