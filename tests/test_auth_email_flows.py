from flask import Flask
from unittest.mock import Mock

import auth.routes as auth_routes
import admin.routes as admin_routes
from admin import admin_bp
from auth import auth_bp
from auth.email_service import EmailService


class FakeCursor:
    def __init__(self, user):
        self.user = user
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=()):
        self.query = str(query)

    def fetchone(self):
        if "FROM Users" in self.query:
            return self.user
        if "COUNT(*)" in self.query:
            return (0,)
        return None


class RegistrationCursor:
    def __init__(self):
        self.query = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=()):
        self.query = str(query)
        self.params = params

    def fetchone(self):
        if "INSERT INTO Users" in self.query:
            return (101,)
        if "FROM EmailVerificationTokens" in self.query:
            code = self.params[0]
            return (1, 101, code, "2099-01-01 00:00:00", 0, 0)
        if "COUNT(*)" in self.query:
            return (0,)
        return None


def _client(monkeypatch, user):
    app = Flask(__name__)
    app.secret_key = "test-secret-key"
    app.register_blueprint(auth_bp, url_prefix="/auth")
    monkeypatch.setattr(auth_routes, "render_template", lambda *args, **kwargs: "login page")
    monkeypatch.setattr(auth_routes, "get_db_cursor", lambda commit=False: FakeCursor(user))
    monkeypatch.setattr(auth_routes, "verify_password", lambda password, password_hash: True)
    monkeypatch.setattr(auth_routes, "log_login_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_routes, "log_audit_event", lambda *args, **kwargs: None)
    return app.test_client()


def _digest(client, purpose, user_id, code):
    from auth.security import otp_digest
    with client.application.app_context():
        return otp_digest(purpose, user_id, code)


def _unverified_user():
    return (
        71, "Old", "Account", "oldaccount", "Old Account", "old@example.com",
        None, None, None, "bcrypt-hash", None, True, False, "Analyst", 0,
        None, None,
    )


def test_unverified_login_does_not_create_pending_session_when_email_fails(monkeypatch):
    client = _client(monkeypatch, _unverified_user())
    monkeypatch.setattr(auth_routes.email_service, "send_verification_email", lambda *args: False)

    response = client.post(
        "/auth/login",
        data={"email_or_username": "old@example.com", "password": "valid-password"},
    )

    assert response.status_code == 200
    with client.session_transaction() as session:
        assert "pending_otp" not in session


def test_unverified_login_redirects_to_code_entry_when_email_succeeds(monkeypatch):
    client = _client(monkeypatch, _unverified_user())
    sent = []
    monkeypatch.setattr(auth_routes.email_service, "send_verification_email", lambda *args: sent.append(args) or True)

    response = client.post(
        "/auth/login",
        data={"email_or_username": "old@example.com", "password": "valid-password"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/verify-otp")
    assert len(sent[0][2]) == 6
    with client.session_transaction() as session:
        # Only a keyed digest is in the (readable) session cookie, never the code.
        assert "otp_code" not in session["pending_otp"]
        assert session["pending_otp"]["otp_hash"] == _digest(client, "email-otp", 71, sent[0][2])


def test_login_rejects_malformed_lockout_timestamp(monkeypatch):
    user = list(_unverified_user())
    user[12] = True
    user[15] = "not-a-timestamp"
    client = _client(monkeypatch, tuple(user))
    send_verification = Mock(return_value=False)
    monkeypatch.setattr(auth_routes.email_service, "send_verification_email", send_verification)

    response = client.post(
        "/auth/login",
        data={"email_or_username": "old@example.com", "password": "valid-password"},
    )

    assert response.status_code == 200
    with client.session_transaction() as session:
        assert "user_id" not in session
        assert "_flashes" in session
    send_verification.assert_not_called()


def test_wrong_password_does_not_establish_a_session(monkeypatch):
    user = list(_unverified_user())
    user[12] = True
    client = _client(monkeypatch, tuple(user))
    monkeypatch.setattr(auth_routes, "verify_password", lambda password, password_hash: False)

    response = client.post(
        "/auth/login",
        data={"email_or_username": "old@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 200
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_resend_verification_sends_six_digit_code_and_starts_otp_session(monkeypatch):
    client = _client(monkeypatch, _unverified_user())
    sent = []
    monkeypatch.setattr(
        auth_routes.email_service,
        "send_verification_email",
        lambda email, name, code: sent.append((email, name, code)) or True,
    )
    audit_messages = []
    monkeypatch.setattr(
        auth_routes,
        "log_audit_event",
        lambda action, details="", user_id=None: audit_messages.append(details),
    )

    response = client.post(
        "/auth/resend-verification",
        data={"email": "old@example.com"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/verify-otp")
    assert len(sent) == 1
    assert len(sent[0][2]) == 6 and sent[0][2].isdigit()
    with client.session_transaction() as session:
        assert "otp_code" not in session["pending_otp"]
        assert session["pending_otp"]["otp_hash"] == _digest(client, "email-otp", 71, sent[0][2])
    assert all(sent[0][2] not in message for message in audit_messages)


def test_otp_resend_fails_closed_when_rate_limit_check_fails(monkeypatch, caplog):
    client = _client(monkeypatch, _unverified_user())
    sent = []
    monkeypatch.setattr(
        auth_routes.email_service,
        "send_verification_email",
        lambda email, name, code: sent.append((email, name, code)) or True,
    )

    def unavailable_db(commit=False):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(auth_routes, "get_db_cursor", unavailable_db)
    with client.session_transaction() as session:
        session["pending_otp"] = {
            "user_id": 71,
            "email": "old@example.com",
            "full_name": "Old Account",
            "otp_hash": "previous-digest",
            "attempts": 0,
        }

    response = client.post("/auth/resend-otp")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/verify-otp")
    assert sent == []
    assert "Could not check OTP resend rate limit" in caplog.text


def test_otp_resend_persists_new_code_before_sending(monkeypatch):
    client = _client(monkeypatch, _unverified_user())
    sent = []
    monkeypatch.setattr(
        auth_routes.email_service,
        "send_verification_email",
        lambda email, name, code: sent.append((email, name, code)) or True,
    )
    with client.session_transaction() as session:
        session["pending_otp"] = {
            "user_id": 71,
            "email": "old@example.com",
            "full_name": "Old Account",
            "otp_hash": "previous-digest",
            "attempts": 2,
        }

    response = client.post("/auth/resend-otp")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/verify-otp")
    assert len(sent) == 1
    assert len(sent[0][2]) == 6 and sent[0][2].isdigit()
    with client.session_transaction() as session:
        assert "otp_code" not in session["pending_otp"]
        assert session["pending_otp"]["otp_hash"] == _digest(client, "email-otp", 71, sent[0][2])
        assert session["pending_otp"]["attempts"] == 0


def test_otp_resend_does_not_send_when_token_persistence_fails(monkeypatch, caplog):
    client = _client(monkeypatch, _unverified_user())
    sent = []
    monkeypatch.setattr(
        auth_routes.email_service,
        "send_verification_email",
        lambda email, name, code: sent.append((email, name, code)) or True,
    )
    original_get_db_cursor = auth_routes.get_db_cursor

    def fail_commit(commit=False):
        if commit:
            raise RuntimeError("database unavailable")
        return original_get_db_cursor(commit=commit)

    monkeypatch.setattr(auth_routes, "get_db_cursor", fail_commit)
    with client.session_transaction() as session:
        session["pending_otp"] = {
            "user_id": 71,
            "email": "old@example.com",
            "full_name": "Old Account",
            "otp_hash": "previous-digest",
            "attempts": 0,
        }

    response = client.post("/auth/resend-otp")

    assert response.status_code == 302
    assert sent == []
    with client.session_transaction() as session:
        assert session["pending_otp"]["otp_hash"] == "previous-digest"
    assert "Could not persist resent email OTP" in caplog.text


def test_registration_and_email_otp_verification(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-secret-key"
    app.register_blueprint(auth_bp, url_prefix="/auth")
    monkeypatch.setattr(auth_routes, "render_template", lambda *args, **kwargs: "page")
    monkeypatch.setattr(auth_routes, "get_db_cursor", lambda commit=False: RegistrationCursor())
    monkeypatch.setattr(auth_routes, "trigger_ai_event", lambda *args, **kwargs: None)
    audit_messages = []
    monkeypatch.setattr(
        auth_routes,
        "log_audit_event",
        lambda action, details="", user_id=None: audit_messages.append(details),
    )
    sent = []
    monkeypatch.setattr(
        auth_routes.email_service,
        "send_verification_email",
        lambda email, name, code: sent.append(code) or True,
    )
    client = app.test_client()

    response = client.post(
        "/auth/signup",
        data={
            "first_name": "Taylor",
            "last_name": "Analyst",
            "email": "taylor@example.com",
            "password": "SeleniumPass123!",
            "confirm_password": "SeleniumPass123!",
            "terms": "on",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/verify-otp")
    assert len(sent) == 1 and len(sent[0]) == 6 and sent[0].isdigit()
    assert all(sent[0] not in message for message in audit_messages)

    response = client.post("/auth/verify-otp", data={"otp_code": sent[0]})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/login")
    with client.session_transaction() as session:
        assert "pending_otp" not in session


def test_forgot_password_keeps_anti_enumeration_response(monkeypatch):
    client = _client(monkeypatch, _unverified_user())
    sent = []
    monkeypatch.setattr(
        auth_routes.email_service,
        "send_password_reset_email",
        lambda *args: sent.append(args) or True,
    )
    monkeypatch.setattr(auth_routes, "trigger_ai_event", lambda *args, **kwargs: None)

    response = client.post(
        "/auth/forgot-password",
        data={"email": "old@example.com"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/reset-password/verify")
    assert len(sent) == 1


def test_admin_login_requires_and_accepts_email_otp(monkeypatch):
    admin = (
        99, "Root Admin", "bcrypt-hash", "SuperAdmin", True, 0, None,
    )
    app = Flask(__name__)
    app.secret_key = "test-secret-key"
    app.register_blueprint(admin_bp)
    monkeypatch.setattr(admin_routes, "render_template", lambda *args, **kwargs: "admin page")
    monkeypatch.setattr(admin_routes, "get_db_cursor", lambda commit=False: FakeCursor(admin))
    monkeypatch.setattr(admin_routes, "verify_password", lambda password, password_hash: True)
    monkeypatch.setattr(admin_routes, "log_login_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(admin_routes, "log_audit_event", lambda *args, **kwargs: None)
    mailed = []
    monkeypatch.setattr(
        EmailService,
        "send_admin_login_otp_email",
        lambda self, *args, **kwargs: mailed.append(kwargs.get("otp_code") or args[2]) or True,
    )
    client = app.test_client()

    response = client.post(
        "/admin/login",
        data={"email": "admin@example.com", "password": "valid-password"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/verify-otp")
    otp_code = mailed[0]
    assert len(otp_code) == 6 and otp_code.isdigit()
    with client.session_transaction() as session:
        # The emailed code must not be readable from the signed-but-unencrypted cookie.
        assert "otp_code" not in session["pending_admin_otp"]
        assert otp_code not in str(dict(session))
        assert "admin_user_id" not in session

    response = client.post("/admin/verify-otp", data={"otp_code": "000000" if otp_code != "000000" else "111111"})
    with client.session_transaction() as session:
        assert "admin_user_id" not in session

    response = client.post("/admin/verify-otp", data={"otp_code": otp_code})

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["admin_user_id"] == 99
        assert session["user_id"] == 99
        assert "pending_admin_otp" not in session
