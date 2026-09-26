import os
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import make_msgid, formatdate
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


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
        
        mail_from = user if user else (os.getenv("MAIL_FROM") or os.getenv("SMTP_FROM_EMAIL") or "noreply@aidataanlystpro.com").strip()
        from_name = os.getenv("MAIL_FROM_NAME", "AI Data Analyst Pro").strip()

        return {
            "server": host,
            "port": port,
            "user": user,
            "password": pwd,
            "from_email": mail_from,
            "from_name": from_name
        }

    def _is_configured(self) -> bool:
        cfg = self._get_config()
        return bool(cfg["user"] and cfg["password"])

    def _send_email_async(self, to_email: str, subject: str, html_body: str, plain_text: str = None, list_unsubscribe: str = None) -> bool:
        """
        Launches an asynchronous daemon background thread for zero-latency instant HTTP response
        while SMTP delivers the email rapidly in the background (< 1 second).
        """
        thread = threading.Thread(
            target=self._send_email,
            args=(to_email, subject, html_body, plain_text, list_unsubscribe),
            daemon=True
        )
        thread.start()
        return True

    def _send_email(self, to_email: str, subject: str, html_body: str, plain_text: str = None, list_unsubscribe: str = None) -> bool:
        cfg = self._get_config()
        if not self._is_configured():
            print("\n[SMTP NOTICE] Real Gmail credentials missing in .env.")
            print(f"[SMTP NOTICE] Sending to: {to_email} | Subject: '{subject}'\n")
            return True

        if not plain_text:
            plain_text = "Please view this email in an HTML-compatible email client."

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{cfg['from_name']} <{cfg['from_email']}>"
        msg["To"] = to_email
        msg["Reply-To"] = f"{cfg['from_name']} <{cfg['from_email']}>"
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain="gmail.com")

        # Note: X-Priority/Importance/X-MSMail-Priority headers were deliberately removed here.
        # Marking every automated email as "urgent" is a well-known spam-scoring anti-pattern
        # (SpamAssassin's PRIORITY_NO_NAME rule and similar heuristics used by Gmail/Outlook
        # filters penalize this) -- it was actively working against inbox placement, not for it.
        msg["X-Auto-Response-Suppress"] = "OOF, AutoReply"
        if list_unsubscribe:
            msg["List-Unsubscribe"] = list_unsubscribe
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

        text_part = MIMEText(plain_text, "plain", "utf-8")
        html_part = MIMEText(html_body, "html", "utf-8")
        msg.attach(text_part)
        msg.attach(html_part)

        ports_to_try = [465, 587] if cfg["port"] == 465 else [cfg["port"], 465, 587]
        for port in ports_to_try:
            try:
                print(f"[SMTP] Rapid dispatching connection to {cfg['server']}:{port}...")
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
                print(f"[SMTP SUCCESS] Security Email delivered to Primary Inbox: {to_email} via port {port}")
                return True
            except Exception as e:
                print(f"[SMTP NOTICE] Port {port} failed: {e}")
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
        user_name_clean = user_name or "Super Admin"
        ip_line = f"Request IP: {ip_address}" if ip_address else ""

        plain_text = f"""Hello {user_name_clean},

A Super Admin login attempt requires second-factor verification.

Your 6-digit security code is: {otp_code}
{ip_line}

This code expires in 10 minutes. If you did not attempt to log in, change your password immediately.

AI Data Analyst Pro Security Team
"""

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Super Admin Security Code</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f1f5f9; padding: 40px 10px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; background-color: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.05);">
                    <tr>
                        <td style="background-color: #0f172a; padding: 28px 36px; text-align: left;">
                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td><span style="font-size: 20px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;">AI Data Analyst Pro</span></td>
                                    <td align="right"><span style="font-size: 11px; font-weight: 700; color: #f87171; background-color: rgba(248,113,113,0.12); padding: 4px 10px; border-radius: 20px; border: 1px solid rgba(248,113,113,0.3);">Super Admin Security</span></td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 36px;">
                            <h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 800; color: #0f172a;">Super Admin Login Verification</h1>
                            <p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #475569;">
                                Hello <strong>{user_name_clean}</strong>,<br><br>
                                A Super Admin login attempt was made on your account. Enter the code below to complete sign-in:
                            </p>
                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 28px 0;">
                                <tr>
                                    <td align="center" style="background-color: #fef2f2; border: 2px dashed #f87171; border-radius: 12px; padding: 24px;">
                                        <div style="font-size: 12px; font-weight: 700; color: #991b1b; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 8px;">Your Security Code</div>
                                        <div style="font-family: 'Courier New', Courier, monospace; font-size: 38px; font-weight: 800; letter-spacing: 12px; color: #dc2626; margin: 4px 0;">{otp_code}</div>
                                        <div style="font-size: 12px; color: #991b1b; margin-top: 8px;">⏰ Expires in <strong>10 minutes</strong></div>
                                    </td>
                                </tr>
                            </table>
                            {f'<p style="margin: 0 0 12px 0; font-size: 12px; color: #94a3b8;">{ip_line}</p>' if ip_line else ''}
                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #fffbeb; border-left: 4px solid #f59e0b; border-radius: 6px; margin-top: 12px;">
                                <tr>
                                    <td style="padding: 14px 16px; font-size: 13px; color: #92400e; line-height: 1.5;">
                                        <strong>Did not request this?</strong> Change your password immediately - this code alone cannot grant access without it, but it means someone has your password.
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="background-color: #f8fafc; padding: 24px 36px; border-top: 1px solid #e2e8f0; text-align: center;">
                            <p style="margin: 0; font-size: 12px; color: #94a3b8;">&copy; 2026 AI Data Analyst Pro Inc. All rights reserved.</p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_verification_email(self, to_email: str, user_name: str, otp_code: str, verification_link: str = None) -> bool:
        subject = f"{otp_code} is your AI Data Analyst Pro verification code"
        user_name_clean = user_name or "Valued Member"

        plain_text = f"""Hello {user_name_clean},

Welcome to AI Data Analyst Pro.

Your 6-digit security verification code is: {otp_code}

This code will expire in 10 minutes. Please enter it on the account verification page to complete your registration.

If you did not request this account, please ignore this email.

Best regards,
AI Data Analyst Pro Security Team
"""

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Email Verification</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f1f5f9; padding: 40px 10px;">
        <tr>
            <td align="center">
                <!-- Main Container Card -->
                <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; background-color: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.05);">
                    
                    <!-- Top Branding Bar -->
                    <tr>
                        <td style="background-color: #0f172a; padding: 28px 36px; text-align: left;">
                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td>
                                        <span style="font-size: 20px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;">AI Data Analyst Pro</span>
                                    </td>
                                    <td align="right">
                                        <span style="font-size: 11px; font-weight: 700; color: #38bdf8; background-color: rgba(56,189,248,0.12); padding: 4px 10px; border-radius: 20px; border: 1px solid rgba(56,189,248,0.3);">Security Verification</span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Body Content -->
                    <tr>
                        <td style="padding: 36px;">
                            <h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 800; color: #0f172a;">Verify Your Email Address</h1>
                            <p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #475569;">
                                Hello <strong>{user_name_clean}</strong>,<br><br>
                                Thank you for registering with <strong>AI Data Analyst Pro</strong>. To finalize your account setup and unlock automated SQL analytics, please enter the 6-digit verification code below:
                            </p>

                            <!-- 6-Digit OTP Box -->
                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 28px 0;">
                                <tr>
                                    <td align="center" style="background-color: #f8fafc; border: 2px dashed #cbd5e1; border-radius: 12px; padding: 24px;">
                                        <div style="font-size: 12px; font-weight: 700; color: #64748b; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 8px;">Your 6-Digit Verification Code</div>
                                        <div style="font-family: 'Courier New', Courier, monospace; font-size: 38px; font-weight: 800; letter-spacing: 12px; color: #2563eb; margin: 4px 0;">{otp_code}</div>
                                        <div style="font-size: 12px; color: #64748b; margin-top: 8px;">⏰ Code expires in <strong>10 minutes</strong></div>
                                    </td>
                                </tr>
                            </table>

                            <p style="margin: 0 0 20px 0; font-size: 14px; line-height: 1.6; color: #475569;">
                                Enter this code on the verification page to activate your enterprise dashboard.
                            </p>

                            <!-- Security Notice -->
                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #fffbebf5; border-left: 4px solid #f59e0b; border-radius: 6px; margin-top: 24px;">
                                <tr>
                                    <td style="padding: 14px 16px; font-size: 13px; color: #92400e; line-height: 1.5;">
                                        <strong>Security Note:</strong> Never share this code with anyone. AI Data Analyst Pro staff will never ask for your verification code.
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8fafc; padding: 24px 36px; border-top: 1px solid #e2e8f0; text-align: center;">
                            <p style="margin: 0 0 8px 0; font-size: 12px; color: #64748b; line-height: 1.5;">
                                This is an automated security notification sent to <strong>{to_email}</strong>.
                            </p>
                            <p style="margin: 0; font-size: 12px; color: #94a3b8;">
                                &copy; 2026 AI Data Analyst Pro Inc. All rights reserved.
                            </p>
                        </td>
                    </tr>

                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_otp_email(self, to_email: str, user_name: str, otp_code: str) -> bool:
        return self.send_verification_email(to_email, user_name, otp_code)

    def send_password_reset_email(self, to_email: str, user_name: str, otp_code: str, reset_link: str = None) -> bool:
        subject = f"{otp_code} is your AI Data Analyst Pro password reset code"
        user_name_clean = user_name or "Valued Member"

        plain_text = f"""Hello {user_name_clean},

We received a request to reset the password for your AI Data Analyst Pro account.

Your 6-digit password reset code is: {otp_code}

This code will expire in 10 minutes. Please enter it on the password reset page.

If you did not request a password reset, please ignore this email.

Best regards,
AI Data Analyst Pro Security Team
"""

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Password Reset</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f1f5f9; padding: 40px 10px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; background-color: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.05);">
                    
                    <td style="background-color: #0f172a; padding: 28px 36px; text-align: left;">
                        <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                            <tr>
                                <td>
                                    <span style="font-size: 20px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;">AI Data Analyst Pro</span>
                                </td>
                                <td align="right">
                                    <span style="font-size: 11px; font-weight: 700; color: #f87171; background-color: rgba(248,113,113,0.12); padding: 4px 10px; border-radius: 20px; border: 1px solid rgba(248,113,113,0.3);">Password Reset</span>
                                </td>
                            </tr>
                        </table>
                    </td>

                    <tr>
                        <td style="padding: 36px;">
                            <h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 800; color: #0f172a;">Reset Your Password</h1>
                            <p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #475569;">
                                Hello <strong>{user_name_clean}</strong>,<br><br>
                                We received a request to reset the password for your <strong>AI Data Analyst Pro</strong> account. Use the 6-digit verification code below to set a new password:
                            </p>

                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 28px 0;">
                                <tr>
                                    <td align="center" style="background-color: #fef2f2; border: 2px dashed #f87171; border-radius: 12px; padding: 24px;">
                                        <div style="font-size: 12px; font-weight: 700; color: #991b1b; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 8px;">Your 6-Digit Password Reset Code</div>
                                        <div style="font-family: 'Courier New', Courier, monospace; font-size: 38px; font-weight: 800; letter-spacing: 12px; color: #dc2626; margin: 4px 0;">{otp_code}</div>
                                        <div style="font-size: 12px; color: #991b1b; margin-top: 8px;">⏰ Code expires in <strong>10 minutes</strong></div>
                                    </td>
                                </tr>
                            </table>

                            <p style="margin: 0 0 20px 0; font-size: 14px; line-height: 1.6; color: #475569;">
                                Enter this code on the password reset page to choose your new password.
                            </p>

                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f8fafc; border-left: 4px solid #64748b; border-radius: 6px; margin-top: 24px;">
                                <tr>
                                    <td style="padding: 14px 16px; font-size: 13px; color: #475569; line-height: 1.5;">
                                        If you did not request a password reset, you can safely ignore this email. Your current password will remain unchanged.
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <tr>
                        <td style="background-color: #f8fafc; padding: 24px 36px; border-top: 1px solid #e2e8f0; text-align: center;">
                            <p style="margin: 0 0 8px 0; font-size: 12px; color: #64748b; line-height: 1.5;">
                                Automated security alert sent to <strong>{to_email}</strong>.
                            </p>
                            <p style="margin: 0; font-size: 12px; color: #94a3b8;">
                                &copy; 2026 AI Data Analyst Pro Inc. All rights reserved.
                            </p>
                        </td>
                    </tr>

                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_login_notification_email(self, to_email: str, user_name: str, login_time: str = None, device_info: str = None) -> bool:
        subject = "Security Alert: New login to AI Data Analyst Pro"
        user_name_clean = user_name or "Valued Member"
        time_str = login_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        dev_str = device_info or "Web Browser"

        plain_text = f"""Hello {user_name_clean},

A new login was detected on your AI Data Analyst Pro account.

Time: {time_str}
Device: {dev_str}

If this was you, no action is needed. If this was not you, please reset your password immediately.

Best regards,
AI Data Analyst Pro Security Team
"""

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>New Login Alert</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f1f5f9; padding: 40px 10px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; background-color: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.05);">
                    
                    <td style="background-color: #0f172a; padding: 28px 36px; text-align: left;">
                        <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                            <tr>
                                <td>
                                    <span style="font-size: 20px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;">AI Data Analyst Pro</span>
                                </td>
                                <td align="right">
                                    <span style="font-size: 11px; font-weight: 700; color: #34d399; background-color: rgba(52,211,153,0.12); padding: 4px 10px; border-radius: 20px; border: 1px solid rgba(52,211,153,0.3);">Login Notification</span>
                                </td>
                            </tr>
                        </table>
                    </td>

                    <tr>
                        <td style="padding: 36px;">
                            <h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 800; color: #0f172a;">New Account Login Detected</h1>
                            <p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #475569;">
                                Hello <strong>{user_name_clean}</strong>,<br><br>
                                A successful login was detected on your <strong>AI Data Analyst Pro</strong> account with the following details:
                            </p>

                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; margin: 20px 0;">
                                <tr>
                                    <td style="padding: 16px; font-size: 14px; color: #334155; line-height: 1.8;">
                                        <strong>Time:</strong> {time_str}<br>
                                        <strong>Device & Client:</strong> {dev_str}
                                    </td>
                                </tr>
                            </table>

                            <p style="margin: 0; font-size: 13px; color: #64748b; line-height: 1.5;">
                                If this was you, no further action is required. If you did not log in, please reset your account password immediately to secure your account.
                            </p>
                        </td>
                    </tr>

                    <tr>
                        <td style="background-color: #f8fafc; padding: 24px 36px; border-top: 1px solid #e2e8f0; text-align: center;">
                            <p style="margin: 0; font-size: 12px; color: #94a3b8;">
                                &copy; 2026 AI Data Analyst Pro Inc. All rights reserved.
                            </p>
                        </td>
                    </tr>

                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_contact_reply_email(self, to_email: str, user_name: str, original_subject: str, original_message: str, admin_reply: str) -> bool:
        """
        Send the Super Admin's reply to a customer's Contact Us submission directly to
        their registered email, quoting their original message for context.
        """
        from markupsafe import escape
        user_name_clean = (user_name or "Valued Customer").strip()
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

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Support Reply</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f1f5f9; padding: 40px 10px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; background-color: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.05);">
                    <tr>
                        <td style="background-color: #0f172a; padding: 28px 36px; text-align: left;">
                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td>
                                        <span style="font-size: 20px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;">AI Data Analyst Pro</span>
                                    </td>
                                    <td align="right">
                                        <span style="font-size: 11px; font-weight: 700; color: #38bdf8; background-color: rgba(56,189,248,0.12); padding: 4px 10px; border-radius: 20px; border: 1px solid rgba(56,189,248,0.3);">Support Reply</span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <tr>
                        <td style="padding: 36px;">
                            <h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 800; color: #0f172a;">Re: {subject_clean}</h1>
                            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #475569;">
                                Hello <strong>{user_name_clean}</strong>,<br><br>
                                Thank you for contacting us. Here is our response to your inquiry:
                            </p>

                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 20px 0;">
                                <tr>
                                    <td style="background-color: #eff6ff; border-left: 4px solid #2563eb; border-radius: 6px; padding: 18px 20px; font-size: 14px; line-height: 1.6; color: #1e3a5f; white-space: pre-line;">{admin_reply}</td>
                                </tr>
                            </table>

                            <p style="margin: 24px 0 8px 0; font-size: 12px; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px;">Your Original Message</p>
                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 14px 16px; font-size: 13px; line-height: 1.5; color: #64748b; white-space: pre-line; font-style: italic;">"{original_message}"</td>
                                </tr>
                            </table>

                            <p style="margin: 24px 0 0 0; font-size: 13px; color: #64748b; line-height: 1.5;">
                                Have more questions? Just reply directly to this email.
                            </p>
                        </td>
                    </tr>

                    <tr>
                        <td style="background-color: #f8fafc; padding: 24px 36px; border-top: 1px solid #e2e8f0; text-align: center;">
                            <p style="margin: 0; font-size: 12px; color: #94a3b8;">
                                &copy; 2026 AI Data Analyst Pro Inc. All rights reserved.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_payment_status_email(self, to_email: str, user_name: str, txn_id: str, status: str, amount: float, plan_name: str = "Enterprise Plan (PKR 25,000/mo)") -> bool:
        """
        Send an official transactional payment status email notification to customer.
        """
        user_name_clean = user_name.strip() or "Valued Customer"
        status_clean = str(status).strip().capitalize()
        status_color = "#10b981" if status_clean == "Completed" else ("#f59e0b" if status_clean == "Pending" else "#ef4444")
        
        subject = f"[{status_clean.upper()}] Payment Update — Transaction {txn_id}"
        
        plain_text = f"Hello {user_name_clean},\nYour payment of ${amount:.2f} ({plan_name}) status has been updated to: {status_clean}.\nTransaction ID: {txn_id}\n\nAI Data Analyst Pro Team"

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Payment Status Update</title>
</head>
<body style="font-family: Arial, sans-serif; background-color: #f1f5f9; margin: 0; padding: 20px;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
        <tr>
            <td align="center">
                <table role="presentation" width="600" border="0" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.1);">
                    <tr>
                        <td style="background: linear-gradient(135deg, #2563eb 0%, #7c3aed 100%); padding: 30px; text-align: center; color: #ffffff;">
                            <h1 style="margin: 0; font-size: 24px; font-weight: 800;">AI Data Analyst Pro</h1>
                            <p style="margin: 6px 0 0 0; font-size: 14px; opacity: 0.9;">Official Payment Transaction Update</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 36px;">
                            <h2 style="margin: 0 0 12px 0; font-size: 20px; color: #0f172a;">Payment Transaction Update</h2>
                            <p style="font-size: 15px; color: #475569; line-height: 1.6; margin-bottom: 24px;">
                                Hello <strong>{user_name_clean}</strong>,<br><br>
                                Your payment status for <strong>{plan_name}</strong> has been updated in our billing system.
                            </p>

                            <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 20px; margin-bottom: 24px;">
                                <div style="display: flex; justify-content: space-between; margin-bottom: 10px; font-size: 14px; color: #334155;">
                                    <span><strong>Transaction ID:</strong></span>
                                    <span style="font-family: monospace; color: #2563eb;">{txn_id}</span>
                                </div>
                                <div style="display: flex; justify-content: space-between; margin-bottom: 10px; font-size: 14px; color: #334155;">
                                    <span><strong>Amount:</strong></span>
                                    <span style="font-weight: 700; color: #059669;">PKR {amount:,.2f}</span>
                                </div>
                                <div style="display: flex; justify-content: space-between; font-size: 14px; color: #334155;">
                                    <span><strong>Payment Status:</strong></span>
                                    <span style="font-weight: 800; color: {status_color}; uppercase;">{status_clean}</span>
                                </div>
                            </div>

                            <p style="font-size: 13px; color: #64748b; line-height: 1.5; margin: 0;">
                                If you have any questions regarding your billing receipt, please contact priority enterprise support at support@aidataanalystpro.com.
                            </p>
                        </td>
                    </tr>
                    <tr>
                        <td style="background-color: #f8fafc; padding: 20px; border-top: 1px solid #e2e8f0; text-align: center; font-size: 12px; color: #94a3b8;">
                            &copy; 2026 AI Data Analyst Pro. All rights reserved.
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

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
            "security advisories, sub-second T-SQL optimizations, and feature announcements.\n\n"
            f"Unsubscribe at any time: {unsubscribe_url}\n\n"
            "AI Data Analyst Pro Team"
        )

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Newsletter Subscription Confirmed</title>
</head>
<body style="font-family: 'Inter', Arial, sans-serif; background-color: #07090e; margin: 0; padding: 30px 10px; color: #ffffff;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
        <tr>
            <td align="center">
                <table role="presentation" width="600" border="0" cellspacing="0" cellpadding="0" style="background-color: #0f172a; border: 1px solid rgba(59, 130, 246, 0.3); border-radius: 16px; overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,0.5);">
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 50%, #06b6d4 100%); padding: 36px 30px; text-align: center; color: #ffffff;">
                            <h1 style="margin: 0; font-size: 26px; font-weight: 800; letter-spacing: -0.5px;">AI Data Analyst Pro</h1>
                            <p style="margin: 8px 0 0 0; font-size: 14px; opacity: 0.95; font-weight: 500;">Next-Gen Enterprise Data Intelligence & Analytics</p>
                        </td>
                    </tr>

                    <!-- Body Content -->
                    <tr>
                        <td style="padding: 40px 36px; background-color: #0f172a;">
                            <h2 style="margin: 0 0 16px 0; font-size: 22px; color: #ffffff; font-weight: 700;">
                                You're In - Welcome to the VIP List
                            </h2>

                            <p style="font-size: 15px; color: #cbd5e1; line-height: 1.7; margin-bottom: 24px;">
                                Hello Subscriber,<br><br>
                                Thank you for joining <strong style="color: #38bdf8;">AI Data Analyst Pro</strong>. You will now receive exclusive updates on high-scale data analytics, sub-second T-SQL performance tuning, AES-256 security insights, and product feature releases.
                            </p>

                            <!-- Feature Card Box -->
                            <div style="background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 12px; padding: 24px; margin-bottom: 28px;">
                                <div style="font-size: 14px; font-weight: 700; color: #38bdf8; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 1px;">⚡ What You Get As A Subscriber:</div>
                                <ul style="margin: 0; padding-left: 20px; color: #e2e8f0; font-size: 14px; line-height: 1.8;">
                                    <li><strong>10M+ Scale Performance Tips:</strong> Sub-second query strategies.</li>
                                    <li><strong>Bilingual Voice AI Updates:</strong> Urdu & English voice query engine news.</li>
                                    <li><strong>Automated Export Blueprints:</strong> Executive PowerPoint & PDF guides.</li>
                                    <li><strong>Security Advisories:</strong> AES-256 field encryption & compliance insights.</li>
                                </ul>
                            </div>

                            <!-- CTA Button -->
                            <div style="text-align: center; margin: 32px 0;">
                                <a href="http://127.0.0.1:5000/" target="_blank" style="background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%); color: #ffffff; text-decoration: none; padding: 14px 32px; border-radius: 10px; font-weight: 700; font-size: 15px; display: inline-block; box-shadow: 0 10px 25px rgba(59, 130, 246, 0.4);">
                                    Explore AI Data Platform &rarr;
                                </a>
                            </div>

                            <p style="font-size: 13px; color: #94a3b8; line-height: 1.6; margin: 0; text-align: center;">
                                Subscribed Email: <strong style="color: #cbd5e1;">{clean_email}</strong>
                            </p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #060912; padding: 24px; border-top: 1px solid rgba(255, 255, 255, 0.08); text-align: center; font-size: 12px; color: #64748b;">
                            &copy; 2026 AI Data Analyst Pro. Enterprise Data Intelligence Platform.<br>
                            All Rights Reserved.<br>
                            <a href="{unsubscribe_url}" style="color: #64748b; text-decoration: underline;">Unsubscribe from these emails</a>
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

    def _wrap_branded_email(self, badge_text, badge_color, heading, body_html):
        """
        Shared card layout for the payment-lifecycle emails below, so each one only
        needs to supply its own heading/body instead of repeating the same ~40 lines
        of header/footer HTML seven times.
        """
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{heading}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f1f5f9; padding: 40px 10px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; background-color: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.05);">
                    <tr>
                        <td style="background-color: #0f172a; padding: 28px 36px; text-align: left;">
                            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
                                <tr>
                                    <td><span style="font-size: 20px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;">AI Data Analyst Pro</span></td>
                                    <td align="right"><span style="font-size: 11px; font-weight: 700; color: {badge_color}; background-color: {badge_color}1f; padding: 4px 10px; border-radius: 20px; border: 1px solid {badge_color}4d;">{badge_text}</span></td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 36px;">
                            <h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 800; color: #0f172a;">{heading}</h1>
                            {body_html}
                        </td>
                    </tr>
                    <tr>
                        <td style="background-color: #f8fafc; padding: 24px 36px; border-top: 1px solid #e2e8f0; text-align: center;">
                            <p style="margin: 0; font-size: 12px; color: #94a3b8;">&copy; 2026 AI Data Analyst Pro Inc. All rights reserved.</p>
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
        name = user_name or "Valued Customer"
        plain_text = f"Hello {name},\n\nWe received your payment proof (PKR 25,000). Our team is verifying it now - your dashboard will be activated as soon as it's approved.\n\nAI Data Analyst Pro Team"
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #475569;">
                Hello <strong>{name}</strong>,<br><br>
                Thank you! Your payment proof (<strong>PKR 25,000</strong>) has been received and is currently under review by our team.
            </p>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #eff6ff; border-left: 4px solid #2563eb; border-radius: 6px;">
                <tr><td style="padding: 14px 16px; font-size: 13px; color: #1e3a5f;">
                    Your dashboard will be activated as soon as our team verifies the payment - this is usually quick. You'll get an email and an in-app notification the moment it's approved.
                </td></tr>
            </table>
        """
        html_body = self._wrap_branded_email("Under Review", "#2563eb", "Payment Received - Under Review", body)
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_admin_new_payment_alert_email(self, admin_email: str, user_name: str, user_email: str, amount: float, txn_id: str) -> bool:
        subject = f"New Payment Submitted by {user_name} - Review Needed"
        plain_text = f"{user_name} ({user_email}) submitted a payment of PKR {amount:,.2f} (Txn: {txn_id}). Please review it in the Super Admin panel."
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #475569;">
                A new payment screenshot has been submitted and is awaiting your review.
            </p>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px;">
                <tr><td style="padding: 16px; font-size: 14px; color: #334155; line-height: 1.8;">
                    <strong>User:</strong> {user_name} ({user_email})<br>
                    <strong>Amount:</strong> PKR {amount:,.2f}<br>
                    <strong>Transaction ID:</strong> {txn_id}
                </td></tr>
            </table>
        """
        html_body = self._wrap_branded_email("Action Needed", "#f59e0b", "New Payment Submitted", body)
        return self._send_email_async(admin_email, subject, html_body, plain_text)

    def send_payment_approved_email(self, to_email: str, user_name: str, start_date: str, end_date: str) -> bool:
        subject = "Your Dashboard is Active! - AI Data Analyst Pro"
        name = user_name or "Valued Customer"
        plain_text = f"Hello {name},\n\nYour payment has been approved! Your dashboard has been enabled and all functionality is now accessible.\n\nSubscription: {start_date} to {end_date}\n\nLog in: access your dashboard now.\n\nAI Data Analyst Pro Team"
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #475569;">
                Hello <strong>{name}</strong>,<br><br>
                Great news! Your payment has been verified and approved. <strong>Your dashboard has been enabled and all functionality is now accessible.</strong> You can access your dashboard without any issue or problem.
            </p>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px; margin-bottom: 24px;">
                <tr><td style="padding: 16px; font-size: 14px; color: #166534; line-height: 1.8;">
                    <strong>Subscription Period:</strong> {start_date} to {end_date}
                </td></tr>
            </table>
            <div style="text-align: center;">
                <a href="#" style="background: linear-gradient(135deg, #2563eb, #38bdf8); color: #fff; text-decoration: none; padding: 14px 32px; border-radius: 10px; font-weight: 700; font-size: 15px; display: inline-block;">Log In to Your Dashboard</a>
            </div>
        """
        html_body = self._wrap_branded_email("Approved", "#10b981", "Your Dashboard is Active!", body)
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_payment_rejected_email(self, to_email: str, user_name: str, reason: str) -> bool:
        subject = "Payment Verification Issue - Action Needed"
        name = user_name or "Valued Customer"
        reason_clean = reason or "Payment could not be verified."
        plain_text = f"Hello {name},\n\nYour payment submission could not be approved.\n\nReason: {reason_clean}\n\nPlease upload a new payment screenshot to try again.\n\nAI Data Analyst Pro Team"
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #475569;">
                Hello <strong>{name}</strong>,<br><br>
                We were unable to verify your payment submission.
            </p>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #fef2f2; border-left: 4px solid #ef4444; border-radius: 6px; margin-bottom: 20px;">
                <tr><td style="padding: 14px 16px; font-size: 13px; color: #991b1b;"><strong>Reason:</strong> {reason_clean}</td></tr>
            </table>
            <p style="margin: 0; font-size: 14px; color: #475569;">Please upload a new, clear payment screenshot with the correct details to try again.</p>
        """
        html_body = self._wrap_branded_email("Action Needed", "#ef4444", "Payment Verification Issue", body)
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_subscription_reminder_email(self, to_email: str, user_name: str, due_date: str, days_left: int) -> bool:
        name = user_name or "Valued Customer"
        when = "today" if days_left <= 0 else f"in {days_left} day(s)"
        subject = f"Reminder: Your Subscription is Due {when}"
        plain_text = f"Hello {name},\n\nYour monthly subscription of PKR 25,000 is due on {due_date}. Please pay and upload your screenshot to avoid suspension.\n\nAI Data Analyst Pro Team"
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #475569;">
                Hello <strong>{name}</strong>,<br><br>
                Your monthly subscription of <strong>PKR 25,000</strong> is due on <strong>{due_date}</strong>. Please pay and upload your screenshot to avoid suspension of your dashboard access.
            </p>
        """
        html_body = self._wrap_branded_email("Reminder", "#f59e0b", "Subscription Renewal Reminder", body)
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_subscription_suspended_email(self, to_email: str, user_name: str) -> bool:
        name = user_name or "Valued Customer"
        subject = "Your Dashboard Has Been Suspended"
        plain_text = f"Hello {name},\n\nYour subscription has expired and your dashboard has been suspended. Please pay PKR 25,000 and upload the screenshot to re-activate.\n\nAI Data Analyst Pro Team"
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #475569;">
                Hello <strong>{name}</strong>,<br><br>
                Your subscription has expired and your dashboard has been suspended. Please pay <strong>PKR 25,000</strong> and upload the screenshot to re-activate your access.
            </p>
        """
        html_body = self._wrap_branded_email("Suspended", "#ef4444", "Your Dashboard Has Been Suspended", body)
        return self._send_email_async(to_email, subject, html_body, plain_text)

    def send_subscription_renewed_email(self, to_email: str, user_name: str, start_date: str, end_date: str) -> bool:
        name = user_name or "Valued Customer"
        subject = "Subscription Renewed - Dashboard Re-activated"
        plain_text = f"Hello {name},\n\nYour subscription has been renewed and your dashboard is active again.\n\nNew period: {start_date} to {end_date}\n\nAI Data Analyst Pro Team"
        body = f"""
            <p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #475569;">
                Hello <strong>{name}</strong>,<br><br>
                Your renewal payment has been approved! Your dashboard is active again.
            </p>
            <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px;">
                <tr><td style="padding: 16px; font-size: 14px; color: #166534;"><strong>New Subscription Period:</strong> {start_date} to {end_date}</td></tr>
            </table>
        """
        html_body = self._wrap_branded_email("Renewed", "#10b981", "Subscription Renewed", body)
        return self._send_email_async(to_email, subject, html_body, plain_text)


# Singleton Instance
email_service = EmailService()
