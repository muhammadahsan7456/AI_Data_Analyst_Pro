from functools import wraps
from flask import session, redirect, url_for, flash, request
from database.connection import get_db_cursor


def is_user_active_in_db(user_id):
    """Check whether user account is active and not suspended in DB."""
    if not user_id:
        return False
    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT IsActive FROM Users WHERE UserID = ?", (user_id,))
            row = cursor.fetchone()
            if row is not None:
                return bool(row[0])
    except Exception:
        pass
    return True


def login_required(f):
    """
    Decorator to protect routes from unauthorized access.
    Redirects unauthenticated or suspended users to the Login page.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("auth.login", next=request.url))

        if not is_user_active_in_db(user_id):
            session.clear()
            flash("❌ Your account has been suspended by the administrator. Please contact support.", "error")
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

            if not is_user_active_in_db(user_id):
                session.clear()
                flash("❌ Your account has been suspended by the administrator. Please contact support.", "error")
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
