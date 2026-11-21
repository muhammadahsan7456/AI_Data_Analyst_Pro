"""
R-Phase 9.6: numbered PDF invoices for approved payments (user + admin download) and the
email audit (links on PUBLIC_BASE_URL, escaping, honest failure, Brevo sender checks,
one support address). Real blueprints on a temporary SQLite database; SMTP is faked.
"""
import base64
import re
import zlib
from datetime import date

import pytest
from flask import Flask

import auth.email_service as es_mod
from admin import admin_bp
from auth import auth_bp
from auth.email_service import EmailService
from database import connection as db
from frontend.routes import frontend
from utils.invoices import add_one_month, invoice_number

BASE = "https://aidataanalystpro.co.uk"


def pdf_text(data: bytes) -> str:
    """Text strings from a ReportLab PDF (Flate streams), no extra dependency."""
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        raw = m.group(1).strip()
        if raw.endswith(b"~>"):  # ReportLab default: ASCII85 then Flate
            try:
                raw = base64.a85decode(raw, adobe=raw.startswith(b"<~"))
            except ValueError:
                try:
                    raw = base64.a85decode(b"<~" + raw, adobe=True)
                except ValueError:
                    continue
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        out += [s.decode("latin-1") for s in re.findall(rb"\(((?:[^()\\]|\\.)*)\)", raw)]
    return re.sub(r"\s+", " ", "".join(out)).replace("\\(", "(").replace("\\)", ")")


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_file = str(tmp_path / "billing.db")
    monkeypatch.setattr(db, "get_connection", lambda: db.SQLiteConnectionAdapter(db_file))
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.setenv("SUPPORT_EMAIL", "muhammadashan7456@gmail.com")
    monkeypatch.setenv("INVOICE_COMPANY_NAME", "AI Data Analyst Pro")
    monkeypatch.setenv("INVOICE_COMPANY_ADDRESS", "Lahore, Pakistan")
    conn = db.SQLiteConnectionAdapter(db_file)
    db.init_sqlite_db(conn.conn)
    cur = conn.conn.cursor()
    cur.execute("INSERT INTO Users (UserID, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role) "
                "VALUES (1, 'root', 'Root Admin', 'root@example.com', 'x', 1, 1, 'SuperAdmin')")
    cur.execute("INSERT INTO Users (UserID, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role, SubscriptionStatus) "
                "VALUES (5, 'buyer', 'Ayesha <b>Khan</b>', 'buyer@example.com', 'x', 1, 1, 'Analyst', 'pending_approval')")
    cur.execute("INSERT INTO Users (UserID, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role) "
                "VALUES (6, 'other', 'Other User', 'other@example.com', 'x', 1, 1, 'Analyst')")
    cur.execute("INSERT INTO Payments (PaymentID, UserID, Amount, Currency, PaymentMethod, TransactionID, Status, PlanName) "
                "VALUES (7, 5, 1499, 'PKR', 'NayaPay', 'NP-778899', 'Pending', 'Pro Plan (PKR 1,499/mo)')")
    cur.execute("INSERT INTO Payments (PaymentID, UserID, Amount, Currency, PaymentMethod, TransactionID, Status) "
                "VALUES (8, 6, 1499, 'PKR', 'SadaPay', 'SP-1', 'Pending')")
    conn.conn.commit()
    conn.close()

    sent = []

    def fake_send(self, to_email, subject, html_body, plain_text=None, list_unsubscribe=None):
        sent.append({"to": to_email, "subject": subject, "html": html_body, "text": plain_text or ""})
        return env_state["smtp_ok"]

    env_state = {"smtp_ok": True}
    monkeypatch.setattr(EmailService, "_send_email", fake_send)

    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = "t"
    app.jinja_env.globals["csrf_token"] = lambda: ""
    import site_config
    app.jinja_env.globals.update(site_config=site_config, support_email=site_config.support_email(), max_upload_mb=500,
                                 public_root=BASE + "/", canonical_url=BASE + "/")
    app.register_blueprint(frontend)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    return app, db_file, sent, env_state


def _admin(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_user_id=1, admin_user_role="SuperAdmin", admin_user_name="Root", admin_user_email="root@example.com")
    return c


def _user(app, uid, email):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(user_id=uid, user_name="U", user_email=email, user_role="Analyst")
    return c


def _row(db_file, sql, params=()):
    conn = db.SQLiteConnectionAdapter(db_file)
    cur = conn.cursor()
    cur.execute(sql, params)
    r = cur.fetchone()
    conn.close()
    return r


def test_approval_issues_numbered_invoice_and_emails_link(env):
    app, db_file, sent, _ = env
    r = _admin(app).post("/admin/payment/update-status/7", data={"status": "Completed"})
    assert r.status_code == 302
    number, start, end, status = _row(db_file, "SELECT InvoiceNumber, PeriodStart, PeriodEnd, Status FROM Payments WHERE PaymentID = 7")
    assert status == "Completed"
    assert number == f"AIDAP-{date.today().year}-000007"
    assert start == date.today().isoformat() and end == add_one_month(date.today()).isoformat()
    mail = [m for m in sent if m["to"] == "buyer@example.com"]
    assert len(mail) == 1
    assert f"{BASE}/receipts/7/download" in mail[0]["html"] and number in mail[0]["html"]
    assert f"{BASE}/receipts/7/download" in mail[0]["text"]
    assert "<b>Khan</b>" not in mail[0]["html"]  # user-supplied name is escaped

    # Second approval does not extend the plan again.
    _admin(app).post("/admin/payment/update-status/7", data={"status": "Completed"})
    assert _row(db_file, "SELECT PeriodEnd FROM Payments WHERE PaymentID = 7")[0] == end


def test_user_and_admin_download_the_pdf_invoice(env):
    app, db_file, _, _ = env
    _admin(app).post("/admin/payment/update-status/7", data={"status": "Completed"})
    number = _row(db_file, "SELECT InvoiceNumber FROM Payments WHERE PaymentID = 7")[0]
    for client, url in ((_user(app, 5, "buyer@example.com"), "/receipts/7/download"),
                        (_admin(app), "/admin/payment/invoice/7/download")):
        r = client.get(url)
        assert r.status_code == 200 and r.mimetype == "application/pdf"
        assert r.data.startswith(b"%PDF") and r.data.rstrip().endswith(b"%%EOF")
        assert f'filename="Invoice_{number}.pdf"' in r.headers["Content-Disposition"]
        text = pdf_text(r.data)
        for needle in (number, "PKR 1,499.00", "NayaPay", "NP-778899", "AI Data Analyst Pro", "Lahore, Pakistan",
                       "muhammadashan7456@gmail.com", "PAID", date.today().strftime("%d %b %Y"),
                       add_one_month(date.today()).strftime("%d %b %Y")):
            assert needle.replace(" ", "") in text.replace(" ", ""), needle  # line wraps drop spaces
    # Receipts page lists the invoice; on-screen invoice shows number and period.
    page = _user(app, 5, "buyer@example.com").get("/receipts").get_data(as_text=True)
    assert number in page and "Invoice PDF" in page
    page = _user(app, 5, "buyer@example.com").get("/receipts/7").get_data(as_text=True)
    assert number in page and "Lahore, Pakistan" in page


def test_no_invoice_for_pending_or_someone_elses_payment(env):
    app, _, _, _ = env
    r = _user(app, 6, "other@example.com").get("/receipts/8/download")
    assert r.status_code == 302 and b"%PDF" not in r.data  # pending: no invoice yet
    r = _user(app, 6, "other@example.com").get("/receipts/7/download")
    assert r.status_code == 302 and b"%PDF" not in r.data  # not this user's payment
    r = _admin(app).get("/admin/payment/invoice/8/download")
    assert r.status_code == 302


def test_failed_email_is_reported_to_admin(env):
    app, _, _, state = env
    state["smtp_ok"] = False
    c = _admin(app)
    c.post("/admin/payment/update-status/7", data={"status": "Completed"})
    with c.session_transaction() as s:
        flashes = " ".join(m for _, m in s.get("_flashes", []))
    assert "could NOT be sent" in flashes


def test_every_email_type_uses_public_links_and_escapes(env, monkeypatch):
    _, _, sent, _ = env
    monkeypatch.setattr(EmailService, "_send_email_async",
                        lambda self, to, subj, html, text=None, lu=None, wait=False: self._send_email(to, subj, html, text, lu))
    es = EmailService()
    evil = "<script>x</script>"
    es.send_admin_login_otp_email("a@example.com", evil, "123456", "203.0.113.9")
    es.send_verification_email("a@example.com", evil, "123456", BASE + "/auth/verify-email/tok")
    es.send_password_reset_email("a@example.com", evil, "123456", BASE + "/auth/reset-password/verify")
    es.send_welcome_email("a@example.com", evil)
    es.send_payment_received_email("a@example.com", evil)
    es.send_payment_approved_email("a@example.com", evil, "01 Jan 2027", "01 Feb 2027", invoice_url=BASE + "/receipts/1/download", invoice_number="AIDAP-2027-000001")
    es.send_payment_rejected_email("a@example.com", evil, evil)
    es.send_subscription_reminder_email("a@example.com", evil, "01 Feb 2027", 3)
    es.send_subscription_suspended_email("a@example.com", evil)
    es.send_subscription_renewed_email("a@example.com", evil, "01 Jan 2027", "01 Feb 2027")
    es.send_contact_reply_email("a@example.com", evil, evil, evil, evil)
    es.send_admin_new_payment_alert_email("admin@example.com", evil, "a@example.com", 1499, evil)
    assert len(sent) == 12
    for m in sent:
        assert "<script>" not in m["html"], m["subject"]
        assert "127.0.0.1" not in m["html"] + m["text"] and "localhost" not in m["html"] + m["text"], m["subject"]
        for href in re.findall(r'href="([^"]+)"', m["html"]):
            assert href.startswith(BASE) or href.startswith("mailto:muhammadashan7456@gmail.com"), (m["subject"], href)
        other = set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", m["html"])) - {"a@example.com", "admin@example.com", "muhammadashan7456@gmail.com"}
        assert not other, (m["subject"], other)


def test_headers_use_support_reply_to_and_sender_domain(monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def sendmail(self, frm, to, msg): captured.update(frm=frm, msg=msg)

    monkeypatch.setattr(es_mod.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp-relay.brevo.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "relay123@smtp-brevo.com")
    monkeypatch.setenv("SMTP_PASSWORD", "not-a-real-password")
    monkeypatch.setenv("MAIL_FROM", "noreply@aidataanalystpro.co.uk")
    monkeypatch.setenv("SUPPORT_EMAIL", "muhammadashan7456@gmail.com")
    monkeypatch.setattr(es_mod, "load_dotenv", lambda *a, **k: None)
    assert EmailService()._send_email("a@example.com", "s", "<p>x</p>", "x") is True
    assert captured["frm"] == "noreply@aidataanalystpro.co.uk"
    assert "Reply-To: muhammadashan7456@gmail.com" in captured["msg"]
    assert re.search(r"Message-ID: <[^>]+@aidataanalystpro\.co\.uk>", captured["msg"])
    assert EmailService().config_problems() == []
    monkeypatch.delenv("MAIL_FROM")
    monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
    probs = " ".join(EmailService().config_problems())
    assert "MAIL_FROM is not set" in probs and "Brevo rejects" in probs


def test_invoice_number_format():
    assert invoice_number(42, date(2026, 11, 20)) == "AIDAP-2026-000042"
    assert add_one_month(date(2027, 1, 31)) == date(2027, 2, 28)
