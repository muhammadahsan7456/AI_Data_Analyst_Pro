import os
import smtplib
import threading
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.utils import make_msgid, formatdate
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Embedded (CID) rather than linked by URL: a <img src="http://127.0.0.1:...">  would
# render as a broken image for every recipient, since that address isn't reachable from
# outside this machine (and even on a real production domain, many email clients block
# remote images by default until the user clicks "show images"). Attaching the file
# directly into the message and referencing it as cid:logo displays immediately in every
# client, with no external request at all.
LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "favicon.png")
LOGO_CID = "aidataanalystpro_logo"
# Links are built at send time from utils.public_url (PUBLIC_BASE_URL first), never from
# a module-level localhost default.
from utils.public_url import public_base_url
from site_config import support_email
from markupsafe import escape as _escape


def _h(value):
    """HTML-escape a user-supplied value before it goes into an email body."""
    return str(_escape("" if value is None else value))


class EmailService:
    """
    Centralized Enterprise Email Service using Gmail SMTP.
    Includes RFC 2822 anti-spam headers (Message-ID, Date, High-Priority Transactional)
    and executive inline-styled HTML templates for primary Inbox delivery.
    Uses asynchronous background daemon threading for zero-latency rapid email dispatching.
    """

    def __init__(self):
        pass

    def _get_config(self):
        load_dotenv(override=True)
        host = os.path.splitext(str(os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER") or "smtp.gmail.com"))[0]
        host = os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER") or "smtp.gmail.com"
        port = int(os.getenv("SMTP_PORT") or 465)
        user = (os.getenv("SMTP_USERNAME") or os.getenv("SMTP_USER") or "").strip()
        pwd = (os.getenv("SMTP_PASSWORD") or "").strip()
        
        # The "From" address must be a sender VERIFIED in the SMTP provider's dashboard,
        # which is not necessarily the same as the SMTP login (e.g. Brevo's SMTP login is
        # an account-specific relay address like "xxxxx@smtp-brevo.com", while the actual
        # verified sender is a real mailbox like "you@gmail.com"). Sending "From" the login
        # instead of the verified sender gets silently rejected by the provider - this is
        # exactly what broke Super Admin OTP delivery after switching from Gmail SMTP (where
        # login and verified sender happened to be identical, masking the bug) to Brevo.
        mail_from = (os.getenv("MAIL_FROM") or os.getenv("SMTP_FROM_EMAIL") or user or "noreply@aidataanalystpro.co.uk").strip()
        from_name = os.getenv("MAIL_FROM_NAME", "AI Data Analyst Pro").strip()

        return {
            "server": host,
            "port": port,
            "user": user,
            "password": pwd,
            "from_email": mail_from,
            "from_name": from_name
        }

    def config_problems(self) -> list:
        """Startup check of the sender setup (no network). Returns warning strings."""
        cfg = self._get_config()
        problems = []
        if not (cfg["user"] and cfg["password"]):
            problems.append("SMTP_USERNAME/SMTP_PASSWORD are not set: no email (OTP, reset, payment) can be sent.")
        explicit_from = (os.getenv("MAIL_FROM") or os.getenv("SMTP_FROM_EMAIL") or "").strip()
        if not explicit_from:
            problems.append("MAIL_FROM is not set; mail is sent from the SMTP login address. "
                            "Set MAIL_FROM to a sender verified in Brevo (Senders & IP).")
        if cfg["from_email"].lower().endswith("smtp-brevo.com"):
            problems.append(f"The From address {cfg['from_email']} is the Brevo SMTP login, which Brevo rejects as a sender. "
                            "Set MAIL_FROM to your verified sender address.")
        if "@" not in cfg["from_email"]:
            problems.append("MAIL_FROM is not an email address.")
        return problems

    def _is_configured(self) -> bool:
        cfg = self._get_config()
        return bool(cfg["user"] and cfg["password"])

    def _send_email_async(self, to_email: str, subject: str, html_body: str, plain_text: str = None, list_unsubscribe: str = None, wait: bool = False) -> bool:
        """
        Launches an asynchronous daemon background thread for zero-latency instant HTTP response
        while SMTP delivers the email rapidly in the background (< 1 second).

        This always returns True the instant the thread starts - it genuinely cannot report
        the real delivery outcome to its caller, since by design that caller has already
        moved on by the time SMTP actually responds. For the handful of flows where that
        matters because the user is left waiting on something only the email can unlock (an
        OTP code), the call sites use the synchronous _send_email directly instead (see
        send_admin_login_otp_email, send_verification_email, send_password_reset_email).
        For everything else here - notifications about something that already happened
        (a payment status change, a subscription renewal) - the one thing this method can
        still guarantee is that a real failure doesn't vanish into an ephemeral print()
        that only exists in that process's stdout: it's written to AuditLogs directly
        (bypassing auth.security.log_audit_event, which needs a Flask request context this
        background thread doesn't have) so a delivery failure is visible and queryable
        after the fact, the same way the Super Admin OTP failures were discovered in this
        pass by reading AuditLogs history.
        """
        def _run_and_record():
            ok = self._send_email(to_email, subject, html_body, plain_text, list_unsubscribe)
            result["ok"] = ok
            if not ok:
                try:
                    from database.connection import get_db_cursor
                    from database.queries import insert_audit_log
                    with get_db_cursor(commit=True) as cursor:
                        cursor.execute(
                            insert_audit_log(),
                            (None, "EMAIL_SEND_FAILED", f"Background email send failed - to: {to_email}, subject: {subject}", "background-thread")
                        )
                except Exception as log_err:
                    print("[SMTP] Also failed to record EMAIL_SEND_FAILED audit entry:", log_err)

        result = {"ok": False}
        if wait:
            # Admin actions (approve/reject/reply) wait for the real SMTP result so the
            # admin is told honestly when the customer did not get the email.
            _run_and_record()
            return result["ok"]
        thread = threading.Thread(target=_run_and_record, daemon=True)
        thread.start()
        return True

    def _send_email(self, to_email: str, subject: str, html_body: str, plain_text: str = None, list_unsubscribe: str = None) -> bool:
        try:
            cfg = self._get_config()
        except (TypeError, ValueError) as err:
            logger.error("Invalid SMTP configuration (%s).", type(err).__name__)
            return False

        if not cfg["user"] or not cfg["password"]:
            logger.error("SMTP credentials are not configured; transactional email was not sent.")
            return False

        if not plain_text:
            plain_text = "Please view this email in an HTML-compatible email client."

        # Outer container is "related" (not "alternative") so the embedded logo image can
        # sit alongside the text/html alternative pair - "alternative" alone has no way to
        # carry a third, non-alternative part like an inline image.
        msg = MIMEMultipart("related")
        msg["Subject"] = subject
        msg["From"] = f"{cfg['from_name']} <{cfg['from_email']}>"
        msg["To"] = to_email
        # Replies go to the one support mailbox (SUPPORT_EMAIL), not to the sending
        # relay address, and the Message-ID uses the sender's own domain (a gmail.com
        # Message-ID on mail from another domain is a spam signal).
        msg["Reply-To"] = support_email()
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=(cfg["from_email"].rsplit("@", 1)[-1] or "aidataanalystpro.co.uk"))

        # Note: X-Priority/Importance/X-MSMail-Priority headers were deliberately removed here.
        # Marking every automated email as "urgent" is a well-known spam-scoring anti-pattern
        # (SpamAssassin's PRIORITY_NO_NAME rule and similar heuristics used by Gmail/Outlook
        # filters penalize this) -- it was actively working against inbox placement, not for it.
        msg["X-Auto-Response-Suppress"] = "OOF, AutoReply"
        if list_unsubscribe:
            msg["List-Unsubscribe"] = list_unsubscribe
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

        alt_part = MIMEMultipart("alternative")
        text_part = MIMEText(plain_text, "plain", "utf-8")
        html_part = MIMEText(html_body, "html", "utf-8")
        alt_part.attach(text_part)
        alt_part.attach(html_part)
        msg.attach(alt_part)

        try:
            with open(LOGO_PATH, "rb") as logo_file:
                logo_img = MIMEImage(logo_file.read())
            logo_img.add_header("Content-ID", f"<{LOGO_CID}>")
            logo_img.add_header("Content-Disposition", "inline", filename="logo.png")
            msg.attach(logo_img)
        except Exception as logo_err:
            print("[SMTP NOTICE] Could not attach logo image:", logo_err)

        ports_to_try = list(dict.fromkeys([465, 587] if cfg["port"] == 465 else [cfg["port"], 465, 587]))
        for port in ports_to_try:
            try:
                if port == 465:
                    with smtplib.SMTP_SSL(cfg["server"], port, timeout=4) as smtp:
                        smtp.login(cfg["user"], cfg["password"])
                        smtp.sendmail(cfg["from_email"], [to_email], msg.as_string())
                else:
                    with smtplib.SMTP(cfg["server"], port, timeout=4) as smtp:
                        smtp.ehlo()
                        smtp.starttls()
                        smtp.ehlo()
                        smtp.login(cfg["user"], cfg["password"])
                        smtp.sendmail(cfg["from_email"], [to_email], msg.as_string())
                logger.info("SMTP delivery succeeded via port %s.", port)
                return True
            except Exception as e:
                logger.warning(
                    "SMTP delivery failed on port %s (%s): %s",
                    port,
                    type(e).__name__,
                    e,
                )
                continue

        return False

    def send_admin_login_otp_email(self, to_email: str, user_name: str, otp_code: str, ip_address: str = None) -> bool:
        """
        Second-factor verification code for the Super Admin console gateway. Separate from
        the regular user OTP template so a compromised inbox rule or filter targeting one
        can't silently also intercept the other, and so the wording reflects the higher
        stakes of admin access (billing/user-data control) rather than a routine signup.
        """
        subject = f"{otp_code} is your Super Admin security code"
        user_name_clean = _h(user_name or "Super Admin")
        ip_line = f"Request IP: {_h(ip_address)}" if ip_address else ""

        plain_text = f"""Hello {user_name_clean},

A Super Admin login attempt requires second-factor verification.

Your 6-digit security code is: {otp_code}
{ip_line}

This code expires in 10 minutes. If you did not attempt to log in, change your password immediately.

AI Data Analyst Pro Security Team
"""

        body = f"""
            <p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                Hello <strong style="color: #ffffff;">{user_name_clean}</strong>,<br><br>
                A Super Admin login attempt was made on your account. Enter the security code below to complete sign-in:
            </p>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 28px 0;">
                <tr>
                    <td align="center" style="background-color: rgba(30, 41, 59, 0.7); border: 2px dashed rgba(248, 113, 113, 0.6); border-radius: 12px; padding: 24px;">
                        <div style="font-size: 12px; font-weight: 700; color: #f87171; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 8px;">Your Security Code</div>
                        <div style="font-family: 'Courier New', Courier, monospace; font-size: 38px; font-weight: 800; letter-spacing: 12px; color: #f87171; margin: 6px 0;">{otp_code}</div>
                        <div style="font-size: 12px; color: #fca5a5; margin-top: 8px;">Expires in <strong style="color: #ffffff;">10 minutes</strong></div>
                    </td>
                </tr>
            </table>
            {f'<p style="margin: 0 0 16px 0; font-size: 12px; color: #94a3b8;">{ip_line}</p>' if ip_line else ''}
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: rgba(245, 158, 11, 0.12); border-left: 4px solid #f59e0b; border-radius: 6px; margin-top: 16px;">
                <tr>
                    <td style="padding: 14px 16px; font-size: 13px; color: #fde68a; line-height: 1.5;">
                        <strong style="color: #ffffff;">Did not request this?</strong> Change your password immediately — this code alone cannot grant access without it, but it indicates someone may know your password.
                    </td>
                </tr>
            </table>
        """
        html_body = self._wrap_branded_email("Super Admin Security", "#f87171", "Super Admin Login Verification", body)
        return self._send_email(to_email, subject, html_body, plain_text)

    def send_verification_email(self, to_email: str, user_name: str, otp_code: str, verification_link: str = None) -> bool:
        subject = f"{otp_code} is your AI Data Analyst Pro verification code"
        user_name_clean = _h(user_name or "Valued Member")

        plain_text = f"""Hello {user_name_clean},

Welcome to AI Data Analyst Pro.

Your 6-digit security verification code is: {otp_code}

This code will expire in 10 minutes. Please enter it on the account verification page to complete your registration.

If you did not request this account, please ignore this email.

Best regards,
AI Data Analyst Pro Security Team
"""

        body = f"""
            <p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                Hello <strong style="color: #ffffff;">{user_name_clean}</strong>,<br><br>
                Thank you for registering with <strong style="color: #ffffff;">AI Data Analyst Pro</strong>. To finalize your account setup and unlock automated SQL analytics, please enter the 6-digit verification code below:
            </p>

            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 28px 0;">
                <tr>
                    <td align="center" style="background-color: rgba(30, 41, 59, 0.7); border: 2px dashed rgba(56, 189, 248, 0.5); border-radius: 12px; padding: 24px;">
                        <div style="font-size: 12px; font-weight: 700; color: #38bdf8; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 8px;">Your 6-Digit Verification Code</div>
                        <div style="font-family: 'Courier New', Courier, monospace; font-size: 38px; font-weight: 800; letter-spacing: 12px; color: #38bdf8; margin: 6px 0;">{otp_code}</div>
                        <div style="font-size: 12px; color: #94a3b8; margin-top: 8px;">Code expires in <strong style="color: #ffffff;">10 minutes</strong></div>
                    </td>
                </tr>
            </table>

            <p style="margin: 0 0 20px 0; font-size: 14px; line-height: 1.6; color: #cbd5e1;">
                Enter this code on the verification page to activate your enterprise dashboard.
            </p>

            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: rgba(56, 189, 248, 0.08); border-left: 4px solid #38bdf8; border-radius: 6px; margin-top: 24px;">
                <tr>
                    <td style="padding: 14px 16px; font-size: 13px; color: #bae6fd; line-height: 1.5;">
                        <strong style="color: #ffffff;">Security Note:</strong> Never share this code with anyone. AI Data Analyst Pro staff will never ask for your verification code.
                    </td>
                </tr>
            </table>
        """
        html_body = self._wrap_branded_email("Security Verification", "#38bdf8", "Verify Your Email Address", body)
        return self._send_email(to_email, subject, html_body, plain_text)

    def send_otp_email(self, to_email: str, user_name: str, otp_code: str) -> bool:
        return self.send_verification_email(to_email, user_name, otp_code)

    def send_password_reset_email(self, to_email: str, user_name: str, otp_code: str, reset_link: str = None) -> bool:
        subject = f"{otp_code} is your AI Data Analyst Pro password reset code"
        user_name_clean = _h(user_name or "Valued Member")

        plain_text = f"""Hello {user_name_clean},

We received a request to reset the password for your AI Data Analyst Pro account.

Your 6-digit password reset code is: {otp_code}

This code will expire in 10 minutes. Please enter it on the password reset page.

If you did not request a password reset, please ignore this email.

Best regards,
AI Data Analyst Pro Security Team
"""

        body = f"""
            <p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                Hello <strong style="color: #ffffff;">{user_name_clean}</strong>,<br><br>
                We received a request to reset the password for your <strong style="color: #ffffff;">AI Data Analyst Pro</strong> account. Use the 6-digit verification code below to set a new password:
            </p>

            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 28px 0;">
                <tr>
                    <td align="center" style="background-color: rgba(30, 41, 59, 0.7); border: 2px dashed rgba(248, 113, 113, 0.6); border-radius: 12px; padding: 24px;">
                        <div style="font-size: 12px; font-weight: 700; color: #f87171; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 8px;">Your 6-Digit Password Reset Code</div>
                        <div style="font-family: 'Courier New', Courier, monospace; font-size: 38px; font-weight: 800; letter-spacing: 12px; color: #f87171; margin: 6px 0;">{otp_code}</div>
                        <div style="font-size: 12px; color: #fca5a5; margin-top: 8px;">Code expires in <strong style="color: #ffffff;">10 minutes</strong></div>
                    </td>
                </tr>
            </table>

            <p style="margin: 0 0 20px 0; font-size: 14px; line-height: 1.6; color: #cbd5e1;">
                Enter this code on the password reset page to choose your new password.
            </p>

            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: rgba(255, 255, 255, 0.05); border-left: 4px solid #737373; border-radius: 6px; margin-top: 24px;">
                <tr>
                    <td style="padding: 14px 16px; font-size: 13px; color: #94a3b8; line-height: 1.5;">
                        If you did not request a password reset, you can safely ignore this email. Your current password will remain unchanged.
                    </td>
                </tr>
            </table>
        """
        html_body = self._wrap_branded_email("Password Reset", "#f87171", "Reset Your Password", body)
        return self._send_email(to_email, subject, html_body, plain_text)

    def send_login_notification_email(self, to_email: str, user_name: str, login_time: str = None, device_info: str = None) -> bool:
        subject = "Security Alert: New login to AI Data Analyst Pro"
        user_name_clean = _h(user_name or "Valued Member")
        time_str = login_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        dev_str = _h(device_info or "Web Browser")

        plain_text = f"""Hello {user_name_clean},

A new login was detected on your AI Data Analyst Pro account.

Time: {time_str}
Device: {dev_str}

If this was you, no action is needed. If this was not you, please reset your password immediately.

Best regards,
AI Data Analyst Pro Security Team
"""

        body = f"""
            <p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                Hello <strong style="color: #ffffff;">{user_name_clean}</strong>,<br><br>
                A successful login was detected on your <strong style="color: #ffffff;">AI Data Analyst Pro</strong> account with the following details:
            </p>

            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 10px; margin: 20px 0;">
                <tr>
                    <td style="padding: 18px 20px; font-size: 14px; color: #cbd5e1; line-height: 1.8;">
                        <strong style="color: #ffffff;">Time:</strong> <span style="color: #38bdf8;">{time_str}</span><br>
                        <strong style="color: #ffffff;">Device &amp; Client:</strong> <span style="color: #ffffff;">{dev_str}</span>
                    </td>
                </tr>
            </table>

            <p style="margin: 0; font-size: 13px; color: #94a3b8; line-height: 1.5;">
                If this was you, no further action is required. If you did not log in, please reset your account password immediately to secure your account.
            </p>
        """
        html_body = self._wrap_branded_email("Login Notification", "#34d399", "New Account Login Detected", body)
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_contact_reply_email(self, to_email: str, user_name: str, original_subject: str, original_message: str, admin_reply: str, wait: bool = False) -> bool:
        """
        Send the Super Admin's reply to a customer's Contact Us submission directly to
        their registered email, quoting their original message for context.
        """
        from markupsafe import escape
        user_name_clean = _h((user_name or "Valued Customer").strip())
        subject_clean = str(escape((original_subject or "Your Inquiry").strip()))
        original_message = str(escape(original_message or ""))
        admin_reply = str(escape(admin_reply or ""))
        subject = f"Re: {(original_subject or 'Your Inquiry').strip()} - AI Data Analyst Pro Support"

        plain_text = f"""Hello {user_name_clean},

Thank you for contacting AI Data Analyst Pro. Here is our response to your inquiry:

{admin_reply}

---
Your original message:
"{original_message}"
---

If you have further questions, simply reply to this email.

Best regards,
AI Data Analyst Pro Support Team
"""

        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                Hello <strong style="color: #ffffff;">{user_name_clean}</strong>,<br><br>
                Thank you for contacting us. Here is our response to your inquiry:
            </p>

            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 20px 0;">
                <tr>
                    <td style="background-color: rgba(30, 41, 59, 0.7); border-left: 4px solid #3b82f6; border-radius: 6px; padding: 18px 20px; font-size: 14px; line-height: 1.6; color: #ffffff; white-space: pre-line;">{admin_reply}</td>
                </tr>
            </table>

            <p style="margin: 24px 0 8px 0; font-size: 12px; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px;">Your Original Message</p>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                    <td style="background-color: rgba(15, 23, 42, 0.5); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 6px; padding: 14px 16px; font-size: 13px; line-height: 1.5; color: #94a3b8; white-space: pre-line; font-style: italic;">"{original_message}"</td>
                </tr>
            </table>

            <p style="margin: 24px 0 0 0; font-size: 13px; color: #94a3b8; line-height: 1.5;">
                Have more questions? Just reply directly to this email.
            </p>
        """
        html_body = self._wrap_branded_email("Support Reply", "#38bdf8", f"Re: {subject_clean}", body)
        return self._send_email_async(to_email, subject, html_body, plain_text, wait=wait)

    def send_payment_status_email(self, to_email: str, user_name: str, txn_id: str, status: str, amount: float, plan_name: str = "Pro Plan (PKR 1,499/mo)") -> bool:
        """
        Send an official transactional payment status email notification to customer.
        """
        user_name_clean = _h((user_name or "").strip() or "Valued Customer")
        txn_id = _h(txn_id)
        status_clean = str(status).strip().capitalize()
        status_color = "#10b981" if status_clean == "Completed" else ("#f59e0b" if status_clean == "Pending" else "#ef4444")
        
        subject = f"[{status_clean.upper()}] Payment Update — Transaction {txn_id}"
        
        plain_text = f"Hello {user_name_clean},\nYour payment of PKR {amount:,.2f} ({plan_name}) status has been updated to: {status_clean}.\nTransaction ID: {txn_id}\n\nAI Data Analyst Pro Team"

        body = f"""
            <p style="font-size: 15px; color: #e2e8f0; line-height: 1.6; margin-bottom: 24px;">
                Hello <strong style="color: #ffffff;">{user_name_clean}</strong>,<br><br>
                Your payment status for <strong style="color: #ffffff;">{plan_name}</strong> has been updated in our billing system.
            </p>

            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 10px; margin-bottom: 24px;">
                <tr>
                    <td style="padding: 20px; font-size: 14px; line-height: 1.8; color: #cbd5e1;">
                        <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                            <tr>
                                <td style="padding: 6px 0; color: #cbd5e1;"><strong>Transaction ID:</strong></td>
                                <td align="right" style="padding: 6px 0; font-family: monospace; color: #38bdf8;">{txn_id}</td>
                            </tr>
                            <tr>
                                <td style="padding: 6px 0; color: #cbd5e1;"><strong>Amount:</strong></td>
                                <td align="right" style="padding: 6px 0; font-weight: 700; color: #34d399;">PKR {amount:,.2f}</td>
                            </tr>
                            <tr>
                                <td style="padding: 6px 0; color: #cbd5e1;"><strong>Payment Status:</strong></td>
                                <td align="right" style="padding: 6px 0; font-weight: 800; color: {status_color}; text-transform: uppercase;">{status_clean}</td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>

            <p style="font-size: 13px; color: #94a3b8; line-height: 1.5; margin: 0;">
                If you have any questions regarding your billing receipt, please contact support at <a href="mailto:{support_email()}" style="color: #38bdf8; text-decoration: underline;">{support_email()}</a>.
            </p>
        """
        html_body = self._wrap_branded_email("Payment Update", status_color, "Payment Transaction Update", body)
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_newsletter_subscription_email(self, to_email: str, unsubscribe_url: str = None) -> bool:
        """
        Send a high-conversion, executive welcome email to new newsletter subscribers.
        """
        clean_email = to_email.strip().lower()
        subject = "Welcome to AI Data Analyst Pro - Subscription Confirmed"
        unsubscribe_url = unsubscribe_url or "#"

        plain_text = (
            "Thank you for subscribing to AI Data Analyst Pro product updates!\n\n"
            "You are now on the VIP list to receive high-scale data analytics tips, "
            "security advisories, fast T-SQL optimizations, and feature announcements.\n\n"
            f"Unsubscribe at any time: {unsubscribe_url}\n\n"
            "AI Data Analyst Pro Team"
        )

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Newsletter Subscription Confirmed</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0a0a0a; margin: 0; padding: 30px 10px; color: #ffffff;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #0a0a0a;">
        <tr>
            <td align="center">
                <table role="presentation" width="600" border="0" cellspacing="0" cellpadding="0" style="max-width: 600px; background-color: #0a0a0a; border: 1px solid rgba(59, 130, 246, 0.3); border-radius: 16px; overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,0.6);">
                    <!-- Header -->
                    <tr>
                        <td style="background: #0a0a0a; padding: 36px 30px 24px 30px; text-align: center; color: #ffffff; border-bottom: 1px solid rgba(255, 255, 255, 0.08);">
                            <img src="cid:aidataanalystpro_logo" width="44" height="44" alt="" style="border-radius: 10px; margin-bottom: 12px; vertical-align: middle;"><br>
                            <h1 style="margin: 0; font-size: 24px; font-weight: 800; letter-spacing: -0.5px; color: #ffffff;">AI Data Analyst Pro</h1>
                            <p style="margin: 8px 0 0 0; font-size: 14px; font-weight: 500; color: #94a3b8;">Next-Gen Enterprise Data Intelligence &amp; Analytics</p>
                        </td>
                    </tr>

                    <!-- Body Content -->
                    <tr>
                        <td style="padding: 36px 32px; background-color: #0a0a0a; color: #ffffff;">
                            <h2 style="margin: 0 0 16px 0; font-size: 20px; color: #ffffff; font-weight: 700;">
                                You're In — Welcome to the VIP List
                            </h2>

                            <p style="font-size: 15px; color: #e2e8f0; line-height: 1.7; margin-bottom: 24px;">
                                Hello Subscriber,<br><br>
                                Thank you for joining <strong style="color: #ffffff;">AI Data Analyst Pro</strong>. You will now receive exclusive updates on high-scale data analytics, fast T-SQL performance tuning, AES-256 security insights, and product feature releases.
                            </p>

                            <!-- Feature Card Box -->
                            <div style="background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 12px; padding: 22px; margin-bottom: 28px;">
                                <div style="font-size: 13px; font-weight: 700; color: #38bdf8; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 1px;">What You Get As A Subscriber:</div>
                                <ul style="margin: 0; padding-left: 20px; color: #f1f5f9; font-size: 14px; line-height: 1.8;">
                                    <li><strong style="color: #ffffff;">10M+ Scale Performance Tips:</strong> Fast query strategies.</li>
                                    <li><strong style="color: #ffffff;">Bilingual Voice AI Updates:</strong> Urdu &amp; English voice query engine news.</li>
                                    <li><strong style="color: #ffffff;">Automated Export Blueprints:</strong> Executive PowerPoint &amp; PDF guides.</li>
                                    <li><strong style="color: #ffffff;">Security Advisories:</strong> Field-level encryption &amp; compliance insights.</li>
                                </ul>
                            </div>

                            <!-- CTA Button -->
                            <div style="text-align: center; margin: 32px 0;">
                                <a href="{public_base_url()}/" target="_blank" style="background: #2563eb; color: #ffffff; text-decoration: none; padding: 14px 34px; border-radius: 10px; font-weight: 700; font-size: 15px; display: inline-block; box-shadow: 0 10px 25px rgba(37, 99, 235, 0.4);">
                                    Explore AI Data Platform &rarr;
                                </a>
                            </div>

                            <p style="font-size: 13px; color: #94a3b8; line-height: 1.6; margin: 0; text-align: center;">
                                Subscribed Email: <strong style="color: #ffffff;">{clean_email}</strong>
                            </p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #0a0a0a; padding: 24px; border-top: 1px solid rgba(255, 255, 255, 0.08); text-align: center; font-size: 12px; color: #94a3b8; line-height: 1.6;">
                            &copy; 2026 AI Data Analyst Pro Inc. Enterprise Data Intelligence Platform.<br>
                            All Rights Reserved.<br>
                            <a href="{unsubscribe_url}" style="color: #94a3b8; text-decoration: underline;">Unsubscribe from these emails</a>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

        list_unsub = f"<{unsubscribe_url}>" if unsubscribe_url and unsubscribe_url != "#" else None
        return self._send_email_async(clean_email, subject, html_body, plain_text, list_unsubscribe=list_unsub)

    def _wrap_branded_email(self, badge_text, badge_color, heading, body_html, unsubscribe_url=None):
        """
        Shared executive dark card layout for all branded application emails.
        Ensures 100% white/light text and high contrast on dark backgrounds.
        """
        badge_html = ""
        if badge_text:
            badge_html = f"""<span style="font-size: 11px; font-weight: 700; color: {badge_color}; background-color: {badge_color}22; padding: 4px 12px; border-radius: 20px; border: 1px solid {badge_color}55; letter-spacing: 0.5px; text-transform: uppercase;">{badge_text}</span>"""

        unsub_html = ""
        if unsubscribe_url and unsubscribe_url != "#":
            unsub_html = f"""<br><a href="{unsubscribe_url}" style="color: #94a3b8; text-decoration: underline;">Unsubscribe from these emails</a>"""

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{heading}</title>
</head>
<body style="margin: 0; padding: 30px 10px; background-color: #0a0a0a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #ffffff;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #0a0a0a;">
        <tr>
            <td align="center">
                <table role="presentation" width="600" border="0" cellspacing="0" cellpadding="0" style="max-width: 600px; background-color: #0a0a0a; border: 1px solid rgba(59, 130, 246, 0.3); border-radius: 16px; overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,0.6);">
                    <!-- Header -->
                    <tr>
                        <td style="background-color: #0a0a0a; padding: 32px 36px 24px 36px; text-align: left; border-bottom: 1px solid rgba(255, 255, 255, 0.08);">
                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td>
                                        <img src="cid:aidataanalystpro_logo" width="36" height="36" alt="" style="vertical-align: middle; border-radius: 8px; margin-right: 10px;">
                                        <span style="font-size: 20px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px; vertical-align: middle;">AI Data Analyst Pro</span>
                                    </td>
                                    <td align="right">
                                        {badge_html}
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Main Body -->
                    <tr>
                        <td style="padding: 36px; background-color: #0a0a0a; color: #ffffff;">
                            <h1 style="margin: 0 0 18px 0; font-size: 22px; font-weight: 800; color: #ffffff; letter-spacing: -0.3px;">{heading}</h1>
                            {body_html}
                        </td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #0a0a0a; padding: 24px 36px; border-top: 1px solid rgba(255, 255, 255, 0.08); text-align: center;">
                            <p style="margin: 0 0 6px 0; font-size: 12px; color: #94a3b8; line-height: 1.5;">
                                &copy; 2026 AI Data Analyst Pro Inc. Enterprise Data Intelligence Platform.
                            </p>
                            <p style="margin: 0; font-size: 12px; color: #64748b;">
                                All rights reserved.{unsub_html}
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

    def send_payment_received_email(self, to_email: str, user_name: str) -> bool:
        subject = "Payment Received - Under Review | AI Data Analyst Pro"
        name = _h(user_name or "Valued Customer")
        plain_text = f"Hello {name},\n\nWe received your payment proof (PKR 1,499). Our team is verifying it now - your Pro dashboard (500 MB upload access) will be activated as soon as it's approved.\n\nAI Data Analyst Pro Team"
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                Hello <strong style="color: #ffffff;">{name}</strong>,<br><br>
                Thank you! Your payment proof (<strong style="color: #34d399;">PKR 1,499</strong>) has been received and is currently under review by our team.
            </p>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: rgba(30, 41, 59, 0.7); border-left: 4px solid #38bdf8; border-radius: 6px;">
                <tr><td style="padding: 14px 16px; font-size: 13px; color: #e2e8f0; line-height: 1.6;">
                    Your dashboard will be activated as soon as our team verifies the payment — this is usually quick. You'll get an email and an in-app notification the moment it's approved.
                </td></tr>
            </table>
        """
        html_body = self._wrap_branded_email("Under Review", "#38bdf8", "Payment Received — Under Review", body)
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_admin_new_payment_alert_email(self, admin_email: str, user_name: str, user_email: str, amount: float, txn_id: str) -> bool:
        user_name, user_email, txn_id = _h(user_name), _h(user_email), _h(txn_id)
        subject = f"New Payment Submitted by {user_name} - Review Needed"
        plain_text = f"{user_name} ({user_email}) submitted a payment of PKR {amount:,.2f} (Txn: {txn_id}). Please review it in the Super Admin panel."
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                A new payment screenshot has been submitted and is awaiting your review.
            </p>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 10px;">
                <tr><td style="padding: 16px; font-size: 14px; color: #e2e8f0; line-height: 1.8;">
                    <strong style="color: #ffffff;">User:</strong> {user_name} ({user_email})<br>
                    <strong style="color: #ffffff;">Amount:</strong> <span style="color: #34d399; font-weight: 700;">PKR {amount:,.2f}</span><br>
                    <strong style="color: #ffffff;">Transaction ID:</strong> <span style="color: #38bdf8; font-family: monospace;">{txn_id}</span>
                </td></tr>
            </table>
        """
        html_body = self._wrap_branded_email("Action Needed", "#f59e0b", "New Payment Submitted", body)
        return self._send_email_async(admin_email, subject, html_body, plain_text)

    def send_payment_approved_email(self, to_email: str, user_name: str, start_date: str, end_date: str,
                                    invoice_url: str = None, invoice_number: str = None, wait: bool = False) -> bool:
        subject = "Payment approved: your Pro plan is active - AI Data Analyst Pro"
        name = _h(user_name or "Valued Customer")
        invoice_line = f"\n\nInvoice {invoice_number}: {invoice_url}" if invoice_url else ""
        plain_text = f"Hello {name},\n\nYour payment has been approved and your Pro plan is active.\n\nPlan period: {start_date} to {end_date}{invoice_line}\n\nLog in: {public_base_url()}/auth/login\n\nAI Data Analyst Pro Team"
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                Hello <strong style="color: #ffffff;">{name}</strong>,<br><br>
                Great news! Your payment has been verified and approved. <strong style="color: #ffffff;">Your dashboard has been enabled and all functionality is now accessible.</strong> You can access your dashboard without any issue or problem.
            </p>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 10px; margin-bottom: 24px;">
                <tr><td style="padding: 16px; font-size: 14px; color: #6ee7b7; line-height: 1.8;">
                    <strong style="color: #ffffff;">Plan period:</strong> {_h(start_date)} to {_h(end_date)}
                </td></tr>
            </table>
            {f'<p style="margin: 0 0 20px 0; font-size: 14px; color: #cbd5e1;">Your invoice {_h(invoice_number)} is ready: <a href="{_h(invoice_url)}" style="color: #38bdf8; font-weight: 700; text-decoration: underline;">download the PDF invoice</a> (sign in first if asked).</p>' if invoice_url else ''}
            <div style="text-align: center; margin: 28px 0 10px 0;">
                <a href="{public_base_url()}/auth/login" style="background: #2563eb; color: #ffffff; text-decoration: none; padding: 14px 34px; border-radius: 10px; font-weight: 700; font-size: 15px; display: inline-block; box-shadow: 0 10px 25px rgba(37, 99, 235, 0.4);">Log In to Your Dashboard</a>
            </div>
        """
        html_body = self._wrap_branded_email("Approved", "#10b981", "Payment approved", body)
        return self._send_email_async(to_email, subject, html_body, plain_text, wait=wait)

    def send_welcome_email(self, to_email: str, user_name: str, via_google: bool = False) -> bool:
        """Sent once the account is usable: after email verification, or a Google sign-up."""
        subject = "Welcome to AI Data Analyst Pro"
        name = _h(user_name or "there")
        how = "with Google" if via_google else "with your email and password"
        plain_text = (f"Hello {name},\n\nYour free account is ready. Sign in {how} at {public_base_url()}/auth/login\n\n"
                      f"Free plan: files under 1 MB. Pro (PKR 1,499 for 30 days): files up to 500 MB.\n\n"
                      f"Questions: {support_email()}\n\nAI Data Analyst Pro Team")
        body = f"""
            <p style="margin: 0 0 18px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                Hello <strong style="color: #ffffff;">{name}</strong>,<br><br>
                Your free account is ready. Upload a dataset and ask questions about it in plain language.
            </p>
            <p style="margin: 0 0 22px 0; font-size: 14px; line-height: 1.6; color: #cbd5e1;">
                Free plan: files under 1 MB. Pro (PKR 1,499 for 30 days): files up to 500 MB.
            </p>
            <div style="text-align: center; margin: 28px 0 10px 0;">
                <a href="{public_base_url()}/dashboard" style="background: #2563eb; color: #ffffff; text-decoration: none; padding: 14px 34px; border-radius: 10px; font-weight: 700; font-size: 15px; display: inline-block; box-shadow: 0 10px 25px rgba(37, 99, 235, 0.4);">Open your dashboard</a>
            </div>
        """
        html_body = self._wrap_branded_email("Welcome", "#38bdf8", "Your account is ready", body)
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_payment_rejected_email(self, to_email: str, user_name: str, reason: str, wait: bool = False) -> bool:
        subject = "Payment Verification Issue - Action Needed"
        name = _h(user_name or "Valued Customer")
        reason_clean = _h(reason or "Payment could not be verified.")
        plain_text = f"Hello {name},\n\nYour payment submission could not be approved.\n\nReason: {reason_clean}\n\nPlease upload a new payment screenshot to try again.\n\nAI Data Analyst Pro Team"
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                Hello <strong style="color: #ffffff;">{name}</strong>,<br><br>
                We were unable to verify your payment submission.
            </p>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: rgba(239, 68, 68, 0.12); border-left: 4px solid #ef4444; border-radius: 6px; margin-bottom: 20px;">
                <tr><td style="padding: 14px 16px; font-size: 13px; color: #fca5a5; line-height: 1.6;"><strong style="color: #ffffff;">Reason:</strong> {reason_clean}</td></tr>
            </table>
            <p style="margin: 0; font-size: 14px; color: #cbd5e1;">Please upload a new, clear payment screenshot with the correct details to try again.</p>
        """
        html_body = self._wrap_branded_email("Action Needed", "#ef4444", "Payment Verification Issue", body)
        return self._send_email_async(to_email, subject, html_body, plain_text, wait=wait)

    def send_subscription_reminder_email(self, to_email: str, user_name: str, due_date: str, days_left: int) -> bool:
        name = _h(user_name or "Valued Customer")
        when = "today" if days_left <= 0 else f"in {days_left} day(s)"
        subject = f"Reminder: Your Subscription is Due {when}"
        plain_text = f"Hello {name},\n\nYour monthly subscription of PKR 1,499 is due on {due_date}. Please pay and upload your screenshot to keep your 500 MB Pro access.\n\nAI Data Analyst Pro Team"
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                Hello <strong style="color: #ffffff;">{name}</strong>,<br><br>
                Your monthly subscription of <strong style="color: #ffffff;">PKR 1,499</strong> is due on <strong style="color: #38bdf8;">{due_date}</strong>. Please pay and upload your screenshot to keep your 500 MB Pro upload access.
            </p>
        """
        html_body = self._wrap_branded_email("Reminder", "#f59e0b", "Subscription Renewal Reminder", body)
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_subscription_suspended_email(self, to_email: str, user_name: str) -> bool:
        """Plan-expired notice (name kept for existing callers): Pro ended, account is Free."""
        from html import escape
        name = escape(user_name or "Valued Customer")
        renew = public_base_url() + "/subscription/renew"
        subject = "Your Pro plan has ended - you're on the Free plan"
        plain_text = (f"Hello {user_name or 'Valued Customer'},\n\nYour 30-day Pro plan has ended, so your account is back on the Free plan "
                      f"(uploads under 1 MB). Your datasets and history are kept.\n\nRenew Pro (PKR 1,499 / 30 days) for uploads up to 500 MB: {renew}\n\nAI Data Analyst Pro Team")
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                Hello <strong style="color: #ffffff;">{name}</strong>,<br><br>
                Your 30-day Pro plan has ended, so your account is back on the <strong style="color: #ffffff;">Free plan</strong> (uploads under 1 MB).
                Your datasets and history are kept.<br><br>
                Renew Pro (<strong style="color: #ffffff;">PKR 1,499</strong> / 30 days) for uploads up to 500 MB:
                <a href="{renew}" style="color: #38bdf8; text-decoration: underline;">{renew}</a>
            </p>
        """
        html_body = self._wrap_branded_email("Plan ended", "#94a3b8", "Your Pro plan has ended", body)
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_subscription_renewed_email(self, to_email: str, user_name: str, start_date: str, end_date: str, wait: bool = False,
                                        invoice_url: str = None, invoice_number: str = None) -> bool:
        name = _h(user_name or "Valued Customer")
        subject = "Subscription Renewed - Dashboard Re-activated"
        invoice_line = f"\n\nInvoice {invoice_number}: {invoice_url}" if invoice_url else ""
        plain_text = f"Hello {name},\n\nYour subscription has been renewed and your dashboard is active again.\n\nNew period: {start_date} to {end_date}{invoice_line}\n\nAI Data Analyst Pro Team"
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #e2e8f0;">
                Hello <strong style="color: #ffffff;">{name}</strong>,<br><br>
                Your renewal payment has been approved! Your dashboard is active again.
            </p>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 10px;">
                <tr><td style="padding: 16px; font-size: 14px; color: #6ee7b7;"><strong style="color: #ffffff;">New Subscription Period:</strong> {_h(start_date)} to {_h(end_date)}</td></tr>
            </table>
            {f'<p style="margin: 20px 0 0 0; font-size: 14px; color: #cbd5e1;">Your invoice {_h(invoice_number)} is ready: <a href="{_h(invoice_url)}" style="color: #38bdf8; font-weight: 700; text-decoration: underline;">download the PDF invoice</a> (sign in first if asked).</p>' if invoice_url else ''}
        """
        html_body = self._wrap_branded_email("Renewed", "#10b981", "Subscription Renewed", body)
        return self._send_email_async(to_email, subject, html_body, plain_text, wait=wait)


# Singleton Instance
email_service = EmailService()
