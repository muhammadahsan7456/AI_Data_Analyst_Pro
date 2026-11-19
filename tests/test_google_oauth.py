"""
Google sign-in regression tests.

Runs the real auth blueprint against a temporary SQLite database (schema from
database.connection.init_sqlite_db). Only Google's HTTP endpoints are mocked.
"""
from contextlib import contextmanager
from urllib.parse import urlparse, parse_qs

import pytest
from flask import Flask

import auth.routes as auth_routes
import auth.security as auth_security
import auth.decorators as auth_decorators
from auth import auth_bp
from database import connection as db
from frontend.routes import frontend


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_file = str(tmp_path / "oauth.db")
    conn = db.SQLiteConnectionAdapter(db_file)
    db.init_sqlite_db(conn)
    conn.close()

    @contextmanager
    def cursor(commit=False):
        c = db.SQLiteConnectionAdapter(db_file)
        cur = c.cursor()
        try:
            yield cur
            if commit:
                c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    for mod in (auth_routes, auth_security, auth_decorators):
        monkeypatch.setattr(mod, "get_db_cursor", cursor)
    monkeypatch.setattr(auth_routes, "trigger_ai_event", lambda *a, **k: None)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aidataanalystpro.co.uk")

    flask_app = Flask(__name__, template_folder="../templates")
    flask_app.secret_key = "test-secret"
    flask_app.register_blueprint(frontend)
    flask_app.register_blueprint(auth_bp, url_prefix="/auth")
    flask_app.config["SERVER_NAME"] = None
    flask_app.db_cursor = cursor
    return flask_app


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _mock_google(monkeypatch, profile, token_status=200):
    calls = {}

    def fake_post(url, data=None, **kw):
        calls["token"] = data
        return _Resp(token_status, {"access_token": "at"} if token_status == 200 else {"error": "invalid_grant"})

    def fake_get(url, headers=None, **kw):
        return _Resp(200, profile)

    monkeypatch.setattr(auth_routes.requests, "post", fake_post)
    monkeypatch.setattr(auth_routes.requests, "get", fake_get)
    return calls


def _start(client, host="aidataanalystpro.co.uk"):
    r = client.get("/auth/google", base_url=f"https://{host}")
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["Location"]).query)
    return r, q


def test_login_redirect_uses_canonical_https_redirect_uri_and_pkce(app):
    client = app.test_client()
    r, q = _start(client)
    assert r.headers["Location"].startswith("https://accounts.google.com/")
    assert q["redirect_uri"] == ["https://aidataanalystpro.co.uk/auth/google/callback"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"][0]


def test_www_host_is_sent_to_canonical_host_before_state_is_set(app):
    client = app.test_client()
    r = client.get("/auth/google", base_url="https://www.aidataanalystpro.co.uk")
    assert r.status_code == 302
    assert r.headers["Location"] .startswith("https://aidataanalystpro.co.uk/auth/google")
    with client.session_transaction(base_url="https://www.aidataanalystpro.co.uk") as s:
        assert "google_oauth_state" not in s


def test_new_google_user_is_created_free_and_verified(app, monkeypatch):
    calls = _mock_google(monkeypatch, {"email": "New.Person@Example.com", "email_verified": True, "name": "New Person"})
    client = app.test_client()
    _, q = _start(client)
    r = client.get(f"/auth/google/callback?state={q['state'][0]}&code=abc", base_url="https://aidataanalystpro.co.uk")
    assert r.status_code == 302 and r.headers["Location"].endswith("/dashboard"), r.headers.get("Location")
    assert calls["token"]["code_verifier"]
    with app.db_cursor() as c:
        c.execute("SELECT UserID, IsVerified, IsActive, SubscriptionStatus, Role FROM Users WHERE Email = ?", ("new.person@example.com",))
        row = c.fetchone()
        assert row and row[1] == 1 and row[2] == 1 and row[3] == "free" and row[4] == "Analyst"
        c.execute("SELECT COUNT(*) FROM UserProfiles WHERE UserID = ?", (row[0],))
        assert c.fetchone()[0] == 1
        c.execute("SELECT COUNT(*) FROM UserSettings WHERE UserID = ?", (row[0],))
        assert c.fetchone()[0] == 1
    with client.session_transaction(base_url="https://aidataanalystpro.co.uk") as s:
        assert s["user_id"] == row[0]


def test_existing_verified_password_user_is_linked_and_keeps_password(app, monkeypatch):
    with app.db_cursor(commit=True) as c:
        c.execute(
            "INSERT INTO Users (FirstName, LastName, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role, SubscriptionStatus) "
            "VALUES ('Old','User','olduser','Old User','old@example.com','KEEP-HASH',1,1,'Analyst','free')"
        )
    _mock_google(monkeypatch, {"email": "old@example.com", "email_verified": True, "name": "Old User"})
    client = app.test_client()
    _, q = _start(client)
    r = client.get(f"/auth/google/callback?state={q['state'][0]}&code=abc", base_url="https://aidataanalystpro.co.uk")
    assert r.headers["Location"].endswith("/dashboard")
    with app.db_cursor() as c:
        c.execute("SELECT COUNT(*) FROM Users WHERE Email='old@example.com'")
        assert c.fetchone()[0] == 1
        c.execute("SELECT PasswordHash FROM Users WHERE Email='old@example.com'")
        assert c.fetchone()[0] == "KEEP-HASH"


def test_unverified_squatter_password_is_retired_on_google_link(app, monkeypatch):
    with app.db_cursor(commit=True) as c:
        c.execute(
            "INSERT INTO Users (FirstName, LastName, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role, SubscriptionStatus) "
            "VALUES ('Sq','At','squat','Sq At','victim@example.com','SQUATTER-HASH',1,0,'Analyst','free')"
        )
    _mock_google(monkeypatch, {"email": "victim@example.com", "email_verified": True, "name": "Victim"})
    client = app.test_client()
    _, q = _start(client)
    client.get(f"/auth/google/callback?state={q['state'][0]}&code=abc", base_url="https://aidataanalystpro.co.uk")
    with app.db_cursor() as c:
        c.execute("SELECT PasswordHash, IsVerified FROM Users WHERE Email='victim@example.com'")
        pw, verified = c.fetchone()
        assert pw != "SQUATTER-HASH" and verified == 1


def test_unverified_google_email_is_refused(app, monkeypatch):
    _mock_google(monkeypatch, {"email": "x@example.com", "email_verified": False})
    client = app.test_client()
    _, q = _start(client)
    r = client.get(f"/auth/google/callback?state={q['state'][0]}&code=abc", base_url="https://aidataanalystpro.co.uk")
    assert r.headers["Location"].endswith("/auth/login")
    with app.db_cursor() as c:
        c.execute("SELECT COUNT(*) FROM Users WHERE Email='x@example.com'")
        assert c.fetchone()[0] == 0


def test_bad_state_and_token_failure_show_friendly_errors(app, monkeypatch):
    client = app.test_client()
    _start(client)
    r = client.get("/auth/google/callback?state=wrong&code=abc", base_url="https://aidataanalystpro.co.uk")
    assert r.headers["Location"].endswith("/auth/login")
    _mock_google(monkeypatch, {}, token_status=400)
    _, q = _start(client)
    r = client.get(f"/auth/google/callback?state={q['state'][0]}&code=abc", base_url="https://aidataanalystpro.co.uk")
    assert r.headers["Location"].endswith("/auth/login")
    with client.session_transaction(base_url="https://aidataanalystpro.co.uk") as s:
        msgs = " ".join(m for _, m in s.get("_flashes", []))
        assert "couldn't sign you in with Google" in msgs
        assert "invalid_grant" not in msgs


def test_suspended_user_cannot_sign_in_with_google(app, monkeypatch):
    with app.db_cursor(commit=True) as c:
        c.execute(
            "INSERT INTO Users (FirstName, LastName, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role) "
            "VALUES ('S','U','susp','S U','susp@example.com','h',0,1,'Analyst')"
        )
    _mock_google(monkeypatch, {"email": "susp@example.com", "email_verified": True})
    client = app.test_client()
    _, q = _start(client)
    r = client.get(f"/auth/google/callback?state={q['state'][0]}&code=abc", base_url="https://aidataanalystpro.co.uk")
    assert r.headers["Location"].endswith("/auth/login")
    with client.session_transaction(base_url="https://aidataanalystpro.co.uk") as s:
        assert "user_id" not in s


def test_portal_session_of_superadmin_does_not_open_admin_console(monkeypatch):
    from admin import admin_bp
    import admin.decorators as admin_dec
    flask_app = Flask(__name__)
    flask_app.secret_key = "t"
    flask_app.register_blueprint(admin_bp)

    @flask_app.route("/auth/login")
    def _login():
        return "login"

    flask_app.add_url_rule("/auth/login", endpoint="auth.login", view_func=_login)
    client = flask_app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = 1
        s["user_role"] = "SuperAdmin"
    r = client.get("/admin/dashboard")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]


def test_missing_client_config_shows_friendly_message(app, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    client = app.test_client()
    r = client.get("/auth/google", base_url="https://aidataanalystpro.co.uk")
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/login")
    with client.session_transaction(base_url="https://aidataanalystpro.co.uk") as s:
        flashes = [m for _, m in s.get("_flashes", [])]
    assert any("Google sign-in is not available right now" in m for m in flashes)


def test_new_google_user_needs_no_otp_and_no_payment(app, monkeypatch):
    _mock_google(monkeypatch, {"email": "fresh@example.com", "email_verified": True, "name": "Fresh"})
    client = app.test_client()
    _, q = _start(client)
    r = client.get(f"/auth/google/callback?state={q['state'][0]}&code=abc", base_url="https://aidataanalystpro.co.uk")
    assert r.headers["Location"].endswith("/dashboard")
    with client.session_transaction(base_url="https://aidataanalystpro.co.uk") as s:
        assert "pending_otp" not in s
    with app.db_cursor() as c:
        c.execute("SELECT COUNT(*) FROM Payments P JOIN Users U ON U.UserID = P.UserID WHERE U.Email = ?", ("fresh@example.com",))
        assert c.fetchone()[0] == 0
        c.execute("SELECT COUNT(*) FROM EmailVerificationTokens T JOIN Users U ON U.UserID = T.UserID WHERE U.Email = ?", ("fresh@example.com",))
        assert c.fetchone()[0] == 0


def test_redirect_uri_without_public_base_url_uses_request_origin(app, monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    client = app.test_client()
    r = client.get("/auth/google", base_url="https://aidataanalystpro.co.uk")
    q = parse_qs(urlparse(r.headers["Location"]).query)
    assert q["redirect_uri"] == ["https://aidataanalystpro.co.uk/auth/google/callback"]


def test_explicit_google_redirect_uri_overrides(app, monkeypatch):
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://aidataanalystpro.co.uk/auth/google/callback")
    client = app.test_client()
    _, q = _start(client)
    assert q["redirect_uri"] == ["https://aidataanalystpro.co.uk/auth/google/callback"]
