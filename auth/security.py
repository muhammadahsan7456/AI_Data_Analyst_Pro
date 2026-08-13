import re
import secrets
from datetime import datetime, timezone
import bcrypt
from flask import request, session, g

from database.connection import get_db_cursor
from database.queries import insert_audit_log, log_login_event


def hash_password(password: str) -> str:
    """
    Hash a plaintext password using bcrypt with salt.
    """
    if not password:
        raise ValueError("Password cannot be empty.")
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plaintext_password: str, hashed_password: str) -> bool:
    """
    Verify a plaintext password against a bcrypt hash.
    """
    if not plaintext_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            plaintext_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def generate_secure_token() -> str:
    """
    Generate a 32-byte URL-safe cryptographically secure random token.
    """
    return secrets.token_urlsafe(32)


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Validate password against enterprise password rules:
    - Minimum 8 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one number
    - At least one special character
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter (A-Z)."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter (a-z)."
    if not re.search(r"[0-9]", password):
        return False, "Password must contain at least one number (0-9)."
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]", password):
        return False, "Password must contain at least one special character (!@#$%^&*)."
    return True, "Password meets security requirements."


def parse_user_agent(user_agent_str: str) -> dict:
    """
    Parse User-Agent HTTP header to extract Browser, OS, and Device.
    """
    if not user_agent_str:
        return {"browser": "Unknown", "os": "Unknown", "device": "Desktop"}

    ua = user_agent_str.lower()
    
    # OS Detection
    if "windows" in ua: os_name = "Windows"
    elif "mac os" in ua or "macintosh" in ua: os_name = "macOS"
    elif "android" in ua: os_name = "Android"
    elif "iphone" in ua or "ipad" in ua: os_name = "iOS"
    elif "linux" in ua: os_name = "Linux"
    else: os_name = "Other"

    # Browser Detection
    if "edg/" in ua or "edge" in ua: browser_name = "Microsoft Edge"
    elif "chrome" in ua and "safari" in ua and "edg" not in ua: browser_name = "Google Chrome"
    elif "firefox" in ua: browser_name = "Mozilla Firefox"
    elif "safari" in ua and "chrome" not in ua: browser_name = "Apple Safari"
    elif "trident" in ua or "msie" in ua: browser_name = "Internet Explorer"
    else: browser_name = "Other Browser"

    # Device Detection
    if "mobile" in ua: device_type = "Mobile Phone"
    elif "tablet" in ua or "ipad" in ua: device_type = "Tablet"
    else: device_type = "Desktop Computer"

    return {
        "browser": browser_name,
        "os": os_name,
        "device": device_type
    }


def get_client_ip() -> str:
    """
    Get client IP address handling proxies (X-Forwarded-For header).
    """
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def log_login_attempt(user_id: int, status: str):
    """
    Record login event in LoginHistory table.
    """
    ip = get_client_ip()
    ua = request.headers.get("User-Agent", "")
    parsed = parse_user_agent(ua)

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                log_login_event(),
                (
                    user_id,
                    ip,
                    ua[:500],
                    parsed["browser"],
                    parsed["os"],
                    parsed["device"],
                    status
                )
            )
    except Exception as e:
        print("Log Login Error:", e)


def log_audit_event(action: str, details: str = "", user_id: int = None):
    """
    Record system audit log for security compliance.
    """
    if user_id is None:
        user_id = session.get("user_id")

    ip = get_client_ip()

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                insert_audit_log(),
                (user_id, action, details, ip)
            )
    except Exception as e:
        print("Log Audit Error:", e)
