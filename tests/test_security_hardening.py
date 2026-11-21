"""
R-Phase 9.7: security hardening checks against the REAL application object (run.app).

- per-IP rate limits (utils/rate_limit.py + run.py before_request hook), and that a
  client-supplied X-Forwarded-For / X-Real-IP / CF-Connecting-IP cannot move a client
  into a fresh bucket;
- CSRF: every state-changing route (POST/PUT/PATCH/DELETE, deletes included) refuses a
  request without a token, and no view is exempt;
- security headers / CSP (self-hosted fonts, no Google Fonts origin);
- debug off; 404/500 pages show no stack trace; the 500 handler logs the traceback.

run.py is imported once with a temporary SQLite database and the scheduler disabled.
"""
import importlib
import logging
import os
import sys

import pytest

from database import connection as db


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    db_file = str(tmp_path_factory.mktemp("sec") / "sec.db")
    mp = pytest.MonkeyPatch()
    mp.setenv("SECRET_KEY", os.getenv("SECRET_KEY") or "test-only-secret-key-not-used-anywhere")
    mp.setenv("ALLOW_SQLITE_FALLBACK", "true")
    mp.setenv("RATE_LIMITS_ENABLED", "true")
    mp.delenv("TRUST_CLOUDFLARE_IP", raising=False)
    mp.delenv("TRUSTED_PROXY_COUNT", raising=False)
    mp.setattr(db, "get_connection", lambda: db.SQLiteConnectionAdapter(db_file))
    mp.setattr(db, "_db_initialized", False, raising=False)
    import utils.scheduler as sched
    mp.setattr(sched, "start_scheduler", lambda: None)
    sys.modules.pop("run", None)
    run = importlib.import_module("run")
    application = run.app

    @application.route("/__test_boom")
    def _boom():
        raise RuntimeError("secret-internal-detail SELECT * FROM Users")

    yield application
    sys.modules.pop("run", None)
    mp.undo()


@pytest.fixture
def client(app):
    from utils import rate_limit
    rate_limit.reset()
    app.config["WTF_CSRF_ENABLED"] = False
    yield app.test_client()
    app.config["WTF_CSRF_ENABLED"] = True
    rate_limit.reset()


# ---------------------------------------------------------------- rate limits

def _login(c, ip="203.0.113.7", headers=None):
    return c.post("/auth/login", data={"email": "nobody@example.com", "password": "wrong-pass"},
                  environ_base={"REMOTE_ADDR": ip}, headers=headers or {})


def test_login_rate_limit_blocks_eleventh_attempt(client):
    for _ in range(10):
        assert _login(client).status_code != 429
    r = _login(client)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    assert b"Too many attempts" in r.data
    # another address is not affected
    assert _login(client, ip="198.51.100.9").status_code != 429


def test_spoofed_forwarding_headers_do_not_reset_the_bucket(client):
    for _ in range(10):
        _login(client)
    for hdr in ("X-Forwarded-For", "X-Real-IP", "CF-Connecting-IP"):
        r = _login(client, headers={hdr: "192.0.2.55"})
        assert r.status_code == 429, hdr


def test_json_endpoints_get_json_429(client):
    for _ in range(5):
        r = client.post("/contact-submit", json={"full_name": "a", "email": "a@example.com", "message": "hi"},
                        environ_base={"REMOTE_ADDR": "203.0.113.8"})
        assert r.status_code != 429
    r = client.post("/contact-submit", json={"full_name": "a", "email": "a@example.com", "message": "hi"},
                    environ_base={"REMOTE_ADDR": "203.0.113.8"})
    assert r.status_code == 429
    assert r.is_json and r.get_json()["success"] is False


def test_rate_limits_can_be_disabled(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMITS_ENABLED", "false")
    for _ in range(15):
        assert _login(client).status_code != 429


def test_get_client_ip_trusts_cloudflare_only_when_enabled(app, monkeypatch):
    from auth.security import get_client_ip
    hdrs = {"CF-Connecting-IP": "192.0.2.10", "X-Forwarded-For": "192.0.2.11", "X-Real-IP": "192.0.2.12"}
    with app.test_request_context("/", headers=hdrs, environ_base={"REMOTE_ADDR": "203.0.113.20"}):
        assert get_client_ip() == "203.0.113.20"
        monkeypatch.setenv("TRUST_CLOUDFLARE_IP", "true")
        assert get_client_ip() == "192.0.2.10"


def test_no_route_reads_forwarding_headers_directly():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for folder in ("admin", "auth", "frontend", "utils", "api"):
        d = os.path.join(root, folder)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.endswith(".py") or name == "security.py":
                continue
            text = open(os.path.join(d, name), encoding="utf-8").read()
            for hdr in ('"X-Forwarded-For"', '"X-Real-IP"', '"CF-Connecting-IP"'):
                if f"headers.get({hdr}" in text:
                    offenders.append(f"{folder}/{name}: {hdr}")
    assert offenders == []


def test_waitress_trusts_only_the_local_nginx_hop():
    """wsgi.py: real client IP comes from the ONE loopback proxy hop; others are cleared."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, "wsgi.py"), encoding="utf-8").read()
    assert 'trusted_proxy="127.0.0.1"' in text
    assert "trusted_proxy_count=1" in text
    assert "clear_untrusted_proxy_headers=True" in text


# ---------------------------------------------------------------- CSRF

def test_every_state_changing_route_requires_csrf(app):
    from utils import rate_limit
    from flask_wtf.csrf import CSRFProtect
    app.config["WTF_CSRF_ENABLED"] = True
    csrf = app.extensions["csrf"]
    assert isinstance(csrf, CSRFProtect)
    assert not csrf._exempt_views and not csrf._exempt_blueprints, "no view may be CSRF-exempt"

    c = app.test_client()
    checked = []
    for rule in app.url_map.iter_rules():
        unsafe = sorted(rule.methods & {"POST", "PUT", "PATCH", "DELETE"})
        if not unsafe or rule.rule.startswith("/static"):
            continue
        url = rule.rule
        for arg in rule.arguments:
            conv = rule._converters.get(arg)
            url = url.replace(f"<int:{arg}>", "1").replace(f"<{arg}>", "x")
            url = url.replace(f"<path:{arg}>", "x").replace(f"<string:{arg}>", "x")
        for method in unsafe:
            rate_limit.reset()
            with c.session_transaction() as s:
                s["user_id"] = 1
                s["admin_user_id"] = 1
                s["_flashes"] = []
            r = c.open(url, method=method, data={"x": "1"}, environ_base={"REMOTE_ADDR": "203.0.113.30"})
            with c.session_transaction() as s:
                flashes = [m for _, m in s.get("_flashes", [])]
            assert r.status_code == 302 and any("session has expired" in m for m in flashes), \
                f"{method} {rule.rule} ({rule.endpoint}) accepted a request without a CSRF token"
            checked.append(rule.endpoint)
    assert "frontend.delete_dataset" in checked
    assert len(checked) > 40


def test_dataset_delete_never_happens_on_get(app, monkeypatch):
    """The delete view itself (login decorator unwrapped) touches nothing on a GET."""
    import frontend.routes as fr
    view = app.view_functions["frontend.delete_dataset"]
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    def _no_db(*a, **k):
        raise AssertionError("delete_dataset touched the database on a GET")
    monkeypatch.setattr(fr, "get_db_cursor", _no_db)
    with app.test_request_context("/dataset/delete/1", method="GET"):
        from flask import session
        session["user_id"] = 1
        r = view(1)
    assert r.status_code == 302 and "/dashboard" in r.headers["Location"]


# ---------------------------------------------------------------- headers, debug, errors

def test_security_headers_and_csp(client):
    r = client.get("/")
    h = r.headers
    csp = h["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp and "frame-ancestors 'self'" in csp and "base-uri 'self'" in csp
    assert "fonts.gstatic.com" not in csp and "fonts.googleapis.com" not in csp
    assert "font-src 'self'" in csp
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "SAMEORIGIN"
    assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in h["Permissions-Policy"]
    assert h["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "waitress" not in h.get("Server", "").lower()
    assert b"fonts.gstatic.com" not in r.data and b"fonts.googleapis.com" not in r.data


def test_google_sign_in_is_a_plain_link_redirect(client):
    r = client.get("/auth/login")
    assert b'href="/auth/google"' in r.data or b"/auth/google" in r.data


def test_debug_is_off(app):
    assert app.debug is False
    assert not app.config.get("PROPAGATE_EXCEPTIONS")


def test_500_page_hides_details_and_logs_traceback(client, caplog):
    with caplog.at_level(logging.ERROR, logger="errors"):
        r = client.get("/__test_boom")
    assert r.status_code == 500
    body = r.data.decode("utf-8", "replace")
    assert "secret-internal-detail" not in body and "Traceback" not in body
    rec = [x for x in caplog.records if x.name == "errors"]
    assert rec and rec[0].exc_info and "secret-internal-detail" in str(rec[0].exc_info[1])


def test_404_page_is_plain(client):
    r = client.get("/no-such-page-xyz")
    assert r.status_code == 404
    assert b"Traceback" not in r.data and b"werkzeug" not in r.data.lower()
