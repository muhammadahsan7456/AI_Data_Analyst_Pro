from functools import wraps
from flask import session, redirect, url_for, flash, request
from database.connection import get_db_cursor


def get_user_access_status(user_id):
    """
    Returns (allowed, status, role) for the given user. status is one of 'active',
    'free', 'pending_approval', 'suspended', 'expired', 'rejected', or 'unknown' (row
    not found / DB error).
    Also re-syncs session["user_role"] with the live DB value on every call, so a role
    change (promotion or demotion) made by an admin takes effect on the target user's
    very next request instead of only after they log out and back in.

    Every subscription state (free / pending_approval / active / expired / suspended /
    rejected) gets full site access - there is a free tier now, so a lapsed or
    not-yet-approved Pro payment must never lock a user out of login/dashboard/Ask AI.
    The only thing those states gate is the upload file-size ceiling, which is enforced
    separately in frontend.upload / frontend.api_upload_file via
    utils.subscription.get_user_subscription_info(). The only reason to block access
    here is IsActive = 0, which is an explicit Super Admin account ban/disable, not a
    subscription state.
    """
    if not user_id:
        return False, "unknown", None
    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT IsActive, ISNULL(SubscriptionStatus, 'free'), Role FROM Users WHERE UserID = ?", (user_id,))
            row = cursor.fetchone()
            if row is None:
                return False, "unknown", None

            is_act, sub_stat, role = row[0], (row[1] or "free").lower(), row[2]
            if session.get("user_id") == user_id and session.get("user_role") != role:
                session["user_role"] = role

            if role in ("SuperAdmin", "Admin"):
                return True, "active", role
            if not is_act:
                return False, "suspended", role
            return True, sub_stat, role
    except Exception:
        pass
    # Fail-open on a transient DB error, matching this decorator's prior behavior -
    # a blip in the connection shouldn't lock every user out of the whole site.
    return True, "active", None


def is_user_active_in_db(user_id):
    """Back-compat wrapper - prefer get_user_access_status() for new code."""
    allowed, _, _ = get_user_access_status(user_id)
    return allowed


def login_required(f):
    """
    Decorator to protect routes from unauthorized access. Redirects unauthenticated
    users to login. A logged-in user is only ever blocked here if their account was
    explicitly disabled/banned by a Super Admin (IsActive = 0) or no longer exists -
    every subscription state (free/pending/active/expired/suspended/rejected) gets
    through, since the free tier means there is no all-or-nothing access gate anymore.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("auth.login", next=request.url))

        allowed, status, _ = get_user_access_status(user_id)
        if not allowed:
            if request.endpoint == "auth.logout":
                return f(*args, **kwargs)
            session.clear()
            flash("Your account has been suspended by the administrator. Please contact support.", "error")
            return redirect(url_for("auth.login"))

        return f(*args, **kwargs)

    return decorated_function


def role_required(*allowed_roles):
    """
    Allows all active authenticated users access to application features.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_id = session.get("user_id")
            if not user_id:
                flash("Please log in to access this page.", "warning")
                return redirect(url_for("auth.login", next=request.url))

            allowed, status, _ = get_user_access_status(user_id)
            if not allowed:
                if request.endpoint == "auth.logout":
                    return f(*args, **kwargs)
                session.clear()
                flash("Your account has been suspended by the administrator. Please contact support.", "error")
                return redirect(url_for("auth.login"))

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def admin_required(f):
    """
    Decorator to protect Super Admin routes.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            flash("Please log in as Administrator to access the Super Admin panel.", "warning")
            return redirect(url_for("auth.login", next=request.url))

        if not is_user_active_in_db(user_id):
            session.clear()
            flash("❌ Your account has been suspended by the administrator. Please contact support.", "error")
            return redirect(url_for("auth.login"))

        user_role = session.get("user_role")
        if user_role not in ["SuperAdmin", "Admin"]:
            flash("Access Denied: Administrator privileges required.", "error")
            return redirect(url_for("frontend.dashboard"))
        return f(*args, **kwargs)

    return decorated_function


def manager_required(f):
    return role_required()(f)


def viewer_allowed(f):
    return role_required()(f)
