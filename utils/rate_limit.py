"""
Per-IP rate limits for sign-in, one-time-code and password-reset endpoints (R-Phase 9.7).

These sit in front of the per-account limits that already live in the database (5 wrong
reset codes, 5 wrong admin OTPs + 15-minute lock, login lockout), and stop one address
from spraying many accounts or hammering the email-sending endpoints.

Counters are kept in this process's memory. That is correct for this deployment, which
runs exactly ONE app process (Waitress, one scheduler thread); a restart clears them, and
the database-backed per-account limits still apply. The client address is
``request.remote_addr``: Waitress (wsgi.py) or ProxyFix (run.py) sets it from the single
trusted Nginx hop, so a client-supplied X-Forwarded-For / X-Real-IP / CF-Connecting-IP
cannot be used to dodge a limit.

Set RATE_LIMITS_ENABLED=false to switch the whole thing off (for load tests only).
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque

# endpoint -> (methods, max requests, window seconds, bucket name)
# Endpoints that share a bucket share one counter (e.g. register + signup).
RULES = {
    "auth.login": ({"POST"}, 10, 300, "login"),
    "auth.register": ({"POST"}, 10, 3600, "register"),
    "auth.signup_route": ({"POST"}, 10, 3600, "register"),
    "auth.register_details": ({"POST"}, 10, 3600, "register"),
    "auth.verify_otp": ({"POST"}, 10, 600, "otp"),
    "auth.resend_otp": ({"POST"}, 5, 900, "otp-send"),
    "auth.resend_verification": ({"POST"}, 5, 900, "otp-send"),
    "auth.forgot_password": ({"POST"}, 5, 900, "reset-send"),
    "auth.reset_password_verify_code": ({"POST"}, 10, 900, "reset"),
    "auth.reset_password": ({"POST"}, 10, 900, "reset"),
    "auth.change_password": ({"POST"}, 10, 900, "change-password"),
    "auth.check_availability": ({"POST"}, 60, 300, "lookup"),
    "auth.check_user_api": ({"GET"}, 60, 300, "lookup"),
    "auth.google_callback": ({"GET"}, 20, 600, "google"),
    "admin.admin_login": ({"POST"}, 5, 900, "admin-login"),
    "admin.admin_verify_otp": ({"POST"}, 10, 900, "admin-otp"),
    "frontend.contact_submit": ({"POST"}, 5, 3600, "contact"),
    "frontend.subscribe_newsletter": ({"POST"}, 5, 3600, "newsletter"),
}

_lock = threading.Lock()
_hits: dict[tuple[str, str], deque] = {}
_last_sweep = 0.0


def enabled() -> bool:
    return os.getenv("RATE_LIMITS_ENABLED", "true").strip().lower() != "false"


def _sweep(now: float) -> None:
    """Drop empty/expired buckets so memory stays bounded."""
    global _last_sweep
    if now - _last_sweep < 60:
        return
    _last_sweep = now
    longest = max(r[2] for r in RULES.values())
    for key in [k for k, q in _hits.items() if not q or now - q[-1] > longest]:
        _hits.pop(key, None)


def check(endpoint: str | None, method: str, ip: str) -> int:
    """Record one request. Returns 0 when allowed, or the seconds to wait when blocked."""
    rule = RULES.get(endpoint or "")
    if not rule or method not in rule[0] or not enabled():
        return 0
    _methods, limit, window, bucket = rule
    now = time.monotonic()
    with _lock:
        _sweep(now)
        q = _hits.setdefault((bucket, ip or "unknown"), deque())
        while q and now - q[0] >= window:
            q.popleft()
        if len(q) >= limit:
            return max(1, int(window - (now - q[0])) + 1)
        q.append(now)
    return 0


def reset() -> None:
    """Clear all counters (tests)."""
    with _lock:
        _hits.clear()
