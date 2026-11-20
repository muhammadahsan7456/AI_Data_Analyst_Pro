"""
R-Phase 9.6: every new account, including Google sign-ups, is on page 1 of the Super
Admin users list straight away; search, filters and database-level pagination work.
Real auth + admin blueprints and templates on a temporary SQLite database; only Google's
HTTP endpoints are mocked.
"""
import re
from urllib.parse import urlparse, parse_qs

import pytest
from flask import Flask

import auth.routes as auth_routes
from admin import admin_bp
from auth import auth_bp
from database import connection as db
from frontend.routes import frontend


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db_file = str(tmp_path / "users_list.db")
    monkeypatch.setattr(db, "get_connection", lambda: db.SQLiteConnectionAdapter(db_file))
    conn = db.SQLiteConnectionAdapter(db_file)
    db.init_sqlite_db(conn.conn)
    cur = conn.conn.cursor()
    cur.execute("INSERT INTO Users (UserID, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role, CreatedAt) "
                "VALUES (1, 'root', 'Root Admin', 'root@example.com', 'x', 1, 1, 'SuperAdmin', '2023-01-01 00:00:00')")
    # 30 older lapsed/suspended accounts: the old sort put all of these ahead of new users.
    for i in range(30):
        cur.execute("INSERT INTO Users (Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role, SubscriptionStatus, CreatedAt) "
                    "VALUES (?, ?, ?, 'x', 1, 1, 'Analyst', 'suspended', ?)",
                    (f"old{i}", f"Old User {i}", f"old{i}@example.com", f"2024-02-{(i % 27) + 1:02d} 10:00:00"))
    conn.conn.commit()
    conn.close()

    monkeypatch.setattr(auth_routes, "trigger_ai_event", lambda *a, **k: None)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aidataanalystpro.co.uk")

    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = "test-secret"
    app.jinja_env.globals["csrf_token"] = lambda: ""
    app.register_blueprint(frontend)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    return app, db_file


def _google_signup(app, monkeypatch, email, name):
    profile = {"sub": "g-123", "email": email, "email_verified": True, "name": name, "picture": ""}
    monkeypatch.setattr(auth_routes.requests, "post", lambda *a, **k: _Resp(200, {"access_token": "at"}))
    monkeypatch.setattr(auth_routes.requests, "get", lambda *a, **k: _Resp(200, profile))
    c = app.test_client()
    r = c.get("/auth/google", base_url="https://aidataanalystpro.co.uk")
    state = parse_qs(urlparse(r.headers["Location"]).query)["state"][0]
    r = c.get(f"/auth/google/callback?state={state}&code=abc", base_url="https://aidataanalystpro.co.uk")
    assert r.status_code == 302 and "/auth/login" not in r.headers["Location"]


def _form_signup(db_file, email):
    from database.queries import create_user
    conn = db.SQLiteConnectionAdapter(db_file)
    cur = conn.cursor()
    cur.execute(create_user(), ("Form", "User", "formuser", "Form User", email, "", "", "", "x"))
    conn.commit()
    conn.close()


def _admin(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["admin_user_id"] = 1
        s["admin_user_role"] = "SuperAdmin"
        s["admin_user_name"] = "Root Admin"
        s["admin_user_email"] = "root@example.com"
    return c


def _emails(html):
    return re.findall(r"[\w.+-]+@example\.com", html)


def test_new_form_and_google_users_are_first_on_page_one(setup, monkeypatch):
    app, db_file = setup
    _form_signup(db_file, "newform@example.com")
    _google_signup(app, monkeypatch, "newgoogle@example.com", "New Google")
    r = _admin(app).get("/admin/users")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    emails = _emails(html)
    assert "newgoogle@example.com" in emails and "newform@example.com" in emails
    first_old = min(i for i, e in enumerate(emails) if e.startswith("old"))
    assert emails.index("newgoogle@example.com") < first_old and emails.index("newform@example.com") < first_old
    assert "33 matching users" in html and "page <strong>1</strong> of <strong>2</strong>" in html
    assert "fa-google" in html  # Google badge on the Google account


def test_search_filters_and_pagination(setup, monkeypatch):
    app, db_file = setup
    _form_signup(db_file, "newform@example.com")
    _google_signup(app, monkeypatch, "newgoogle@example.com", "New Google")
    c = _admin(app)
    html = c.get("/admin/users?q=NEWGOOGLE").get_data(as_text=True)
    assert set(_emails(html)) == {"newgoogle@example.com"}
    html = c.get("/admin/users?signup=google").get_data(as_text=True)
    assert set(_emails(html)) == {"newgoogle@example.com"}
    html = c.get("/admin/users?signup=form&per_page=100").get_data(as_text=True)
    assert "newgoogle@example.com" not in _emails(html) and "newform@example.com" in _emails(html)
    html = c.get("/admin/users?verified=0").get_data(as_text=True)
    assert set(_emails(html)) == {"newform@example.com"}  # form sign-up before OTP
    html = c.get("/admin/users?joined=7").get_data(as_text=True)
    assert set(_emails(html)) == {"newform@example.com", "newgoogle@example.com"}
    html = c.get("/admin/users?status=free").get_data(as_text=True)
    assert "newgoogle@example.com" in _emails(html) and "old1@example.com" not in _emails(html)
    page2 = c.get("/admin/users?page=2").get_data(as_text=True)
    page1 = c.get("/admin/users?page=1").get_data(as_text=True)
    assert len(set(_emails(page1)) & set(_emails(page2)) - {"root@example.com"}) == 0
    assert len(set(_emails(page1)) | set(_emails(page2))) >= 33
    html = c.get("/admin/users?sort=attention").get_data(as_text=True)
    assert _emails(html)[0].startswith("old")  # the old "needs attention" order is still available
    # Unknown filter values are ignored rather than reaching SQL.
    assert c.get("/admin/users?status=x';--&sort=evil&per_page=7").status_code == 200
