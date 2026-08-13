from functools import wraps
from flask import session, redirect, url_for, flash, request, render_template, abort


def login_required(f):
    """
    Decorator to protect routes from unauthorized access.
    Redirects unauthenticated users to the Login page with a message.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)

    return decorated_function


def role_required(*allowed_roles):
    """
    Allows all authenticated users full access to all application features.
    Role restrictions removed as requested.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get("user_id"):
                flash("Please log in to access this page.", "warning")
                return redirect(url_for("auth.login", next=request.url))
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def admin_required(f):
    """
    Allows all authenticated users access.
    """
    return role_required()(f)


def manager_required(f):
    """
    Allows all authenticated users access.
    """
    return role_required()(f)


def viewer_allowed(f):
    """
    Allows all authenticated users access.
    """
    return role_required()(f)
