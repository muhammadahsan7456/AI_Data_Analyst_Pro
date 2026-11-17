import logging
import smtplib

from auth.email_service import EmailService


def test_admin_otp_fails_when_smtp_credentials_are_missing(monkeypatch, caplog):
    service = EmailService()
    monkeypatch.setattr(
        service,
        "_get_config",
        lambda: {"user": "", "password": ""},
    )

    with caplog.at_level(logging.ERROR, logger="auth.email_service"):
        sent = service.send_admin_login_otp_email(
            "admin@example.com",
            "Admin",
            "123456",
        )

    assert sent is False
    assert "SMTP credentials are not configured" in caplog.text


def test_admin_otp_reports_smtp_rejection(monkeypatch, caplog):
    class RejectingSMTP:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def ehlo(self):
            return None

        def starttls(self):
            return None

        def login(self, username, password):
            raise smtplib.SMTPAuthenticationError(535, b"credentials rejected")

    service = EmailService()
    monkeypatch.setattr(
        service,
        "_get_config",
        lambda: {
            "user": "smtp-user",
            "password": "smtp-password",
            "server": "smtp.example.com",
            "port": 465,
            "from_email": "sender@example.com",
            "from_name": "AI Data Analyst Pro",
        },
    )
    monkeypatch.setattr("auth.email_service.smtplib.SMTP_SSL", lambda *args, **kwargs: RejectingSMTP())
    monkeypatch.setattr("auth.email_service.smtplib.SMTP", lambda *args, **kwargs: RejectingSMTP())

    with caplog.at_level(logging.WARNING, logger="auth.email_service"):
        sent = service.send_admin_login_otp_email(
            "admin@example.com",
            "Admin",
            "123456",
        )

    assert sent is False
    assert "credentials rejected" in caplog.text
