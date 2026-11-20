"""
R-Phase 8: every outbound link uses the public origin (PUBLIC_BASE_URL first, never a
hardcoded localhost), and the support address comes from one setting.
"""
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest
from flask import Flask

import auth.routes as auth_routes
import site_config
from auth import auth_bp
from auth.email_service import EmailService
from frontend.routes import frontend
from utils import public_url

REPO = Path(__file__).resolve().parents[1]


class _UserCursor:
    def __init__(self, user):
        self.user, self.query = user, ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=()):
        self.query = str(query)

    def fetchone(self):
        if "FROM Users" in self.query:
            return self.user
        if "COUNT(*)" in self.query:
            return (0,)
        return None


_USER = (71, "Old", "Account", "oldaccount", "Old Account", "old@example.com", None, None,
         None, "hash", None, True, True, "Analyst", 0, None, None)


def _app(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(frontend)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    monkeypatch.setattr(auth_routes, "get_db_cursor", lambda commit=False: _UserCursor(_USER))
    monkeypatch.setattr(auth_routes, "log_audit_event", lambda *a, **k: None)
    monkeypatch.setattr(auth_routes, "trigger_ai_event", lambda *a, **k: None)
    return app


def _reset_link(monkeypatch, **request_kwargs):
    app = _app(monkeypatch)
    sent = []
    monkeypatch.setattr(auth_routes.email_service, "send_password_reset_email",
                        lambda email, name, code, link: sent.append(link) or True)
    app.test_client().post("/auth/forgot-password", data={"email": "old@example.com"}, **request_kwargs)
    assert len(sent) == 1
    return sent[0]


def test_reset_link_uses_public_base_url_not_request_host(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aidataanalystpro.co.uk/")
    link = _reset_link(monkeypatch, base_url="http://127.0.0.1:5000")
    assert link.startswith("https://aidataanalystpro.co.uk/auth/reset-password/")
    assert "127.0.0.1" not in link and "localhost" not in link


def test_untrusted_forwarded_host_is_ignored_without_public_base_url(monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    link = _reset_link(monkeypatch, base_url="http://app.internal:5000",
                       headers={"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"})
    assert link.startswith("http://app.internal:5000/auth/reset-password/")
    assert "evil.example" not in link


def test_trusted_proxy_headers_are_used_through_proxyfix(monkeypatch):
    from werkzeug.middleware.proxy_fix import ProxyFix
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    app = _app(monkeypatch)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    sent = []
    monkeypatch.setattr(auth_routes.email_service, "send_password_reset_email",
                        lambda email, name, code, link: sent.append(link) or True)
    app.test_client().post("/auth/forgot-password", data={"email": "old@example.com"},
                           base_url="http://127.0.0.1:5000",
                           headers={"X-Forwarded-Host": "aidataanalystpro.co.uk", "X-Forwarded-Proto": "https"})
    assert sent[0].startswith("https://aidataanalystpro.co.uk/auth/reset-password/")


def test_google_redirect_uri_follows_public_base_url(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aidataanalystpro.co.uk")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    app = _app(monkeypatch)
    resp = app.test_client().get("/auth/google/login", base_url="https://aidataanalystpro.co.uk")
    q = parse_qs(urlparse(resp.headers["Location"]).query)
    assert q["redirect_uri"] == ["https://aidataanalystpro.co.uk/auth/google/callback"]


def test_email_bodies_link_to_public_origin(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aidataanalystpro.co.uk")
    bodies = []
    monkeypatch.setattr(EmailService, "_send_email", lambda self, to, subj, html, *a, **k: bodies.append(html) or True)
    monkeypatch.setattr(EmailService, "_send_email_async", lambda self, to, subj, html, *a, **k: bodies.append(html) or True)
    svc = EmailService()
    svc.send_payment_approved_email("u@example.com", "U", "2026-11-19", "2026-12-19")
    svc.send_newsletter_subscription_email("u@example.com", unsubscribe_url="https://aidataanalystpro.co.uk/unsub/x")
    assert bodies
    text = "\n".join(bodies)
    assert "https://aidataanalystpro.co.uk/auth/login" in text
    assert "https://aidataanalystpro.co.uk/" in text
    assert "127.0.0.1" not in text and "localhost" not in text


def test_startup_check_flags_missing_and_localhost_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    assert public_url.check_public_base_url_config()
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:5000")
    problems = public_url.check_public_base_url_config()
    assert problems and "points at this machine" in problems[0]
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aidataanalystpro.co.uk")
    assert public_url.check_public_base_url_config() == []


def test_support_email_has_one_default_and_env_override(monkeypatch):
    monkeypatch.delenv("SUPPORT_EMAIL", raising=False)
    assert site_config.support_email() == site_config.DEFAULT_SUPPORT_EMAIL == "muhammadashan7456@gmail.com"
    monkeypatch.setenv("SUPPORT_EMAIL", "help@example.org")
    assert site_config.support_email() == "help@example.org"


def test_payment_email_uses_support_email(monkeypatch):
    monkeypatch.setenv("SUPPORT_EMAIL", "help@example.org")
    bodies = []
    monkeypatch.setattr(EmailService, "_send_email", lambda self, to, subj, html, *a, **k: bodies.append(html) or True)
    monkeypatch.setattr(EmailService, "_send_email_async", lambda self, to, subj, html, *a, **k: bodies.append(html) or True)
    EmailService().send_payment_status_email("u@example.com", "U", "TXN1", "Completed", 1499)
    assert "help@example.org" in "\n".join(bodies)


def test_no_hardcoded_support_address_variants_left():
    out = subprocess.run(
        ["git", "grep", "-n", "-I", "-E", r"support@aidataanalyst|aidataanalystpro\.com|aidataanalyst\.pro",
         "--", "*.py", "*.html", "*.js", "*.example"],
        cwd=REPO, capture_output=True, text=True,
    )
    hits = [l for l in out.stdout.splitlines() if "tests/" not in l]
    assert hits == []
    # The default address itself is written exactly once outside tests.
    out = subprocess.run(["git", "grep", "-n", "-F", site_config.DEFAULT_SUPPORT_EMAIL, "--", "*.py", "*.html"],
                         cwd=REPO, capture_output=True, text=True)
    assert [l for l in out.stdout.splitlines() if "tests/" not in l] == [
        l for l in out.stdout.splitlines() if l.startswith("site_config.py:")
    ]


def test_templates_do_not_build_absolute_urls_from_the_request_host():
    offenders = []
    for path in (REPO / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"request\.(url_root|host_url|url|host)\b", text):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []
