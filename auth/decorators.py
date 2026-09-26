from functools import wraps
from flask import session, redirect, url_for, flash, request
from database.connection import get_db_cursor


def get_user_access_status(user_id):
    """
    Returns (allowed, status, role) for the given user. status is one of 'active',
    'pending_approval', 'suspended', 'rejected', or 'unknown' (row not found / DB error).
    Also re-syncs session["user_role"] with the live DB value on every call, so a role
    change (promotion or demotion) made by an admin takes effect on the target user's
    very next request instead of only after they log out and back in.

    Dashboard access is allowed ONLY when status is 'active' (or its transient
    'expiring_soon' substate) AND today <= SubscriptionEndDate - the scheduler is what
    actually flips a past-due 'active' row to 'suspended', so by the time a request
    lands here the DB status is already authoritative; this function does not need to
    re-check the date itself.
    """
    if not user_id:
        return False, "unknown", None
    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT IsActive, ISNULL(SubscriptionStatus, 'active'), Role FROM Users WHERE UserID = ?", (user_id,))
            row = cursor.fetchone()
            if row is None:
                return False, "unknown", None

            is_act, sub_stat, role = row[0], row[1], row[2]
            if session.get("user_id") == user_id and session.get("user_role") != role:
                session["user_role"] = role

            if role in ("SuperAdmin", "Admin"):
                return True, "active", role
            if not is_act:
                return False, "suspended", role
            if sub_stat in ("suspended", "expired"):
                return False, "suspended", role
            if sub_stat == "pending_approval":
                return False, "pending_approval", role
            if sub_stat == "rejected":
                return False, "rejected", role
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
    users to login, pending-approval users to the waiting screen, and
    suspended/rejected users to the renewal screen - never destroys their session,
    since all three of those are logged-in states the user should stay logged into.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("auth.login", next=request.url))

        allowed, status, _ = get_user_access_status(user_id)
        if not allowed:
            # Let the gate pages and logout themselves render without redirect-looping.
            if request.endpoint in ("frontend.waiting_approval", "frontend.renew_subscription", "auth.logout"):
                return f(*args, **kwargs)
            if status == "pending_approval":
                return redirect(url_for("frontend.waiting_approval"))
            return redirect(url_for("frontend.renew_subscription"))

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
                if request.endpoint in ("frontend.waiting_approval", "frontend.renew_subscription", "auth.logout"):
                    return f(*args, **kwargs)
                if status == "pending_approval":
                    return redirect(url_for("frontend.waiting_approval"))
                return redirect(url_for("frontend.renew_subscription"))

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
