from functools import wraps
from flask import session, flash, redirect, url_for, request, render_template
from database.connection import get_db_cursor

def admin_required(f):
    """
    Decorator enforcing that the current logged-in user possesses SuperAdmin or Admin role.
    If unauthorized, redirects safely to 403 Access Denied.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_id = session.get("admin_user_id") or (session.get("user_id") if session.get("user_role") in ["SuperAdmin", "Admin"] else None)
        if not admin_id:
            flash("Super Admin authentication required to access executive console.", "warning")
            return redirect(url_for("admin.admin_login", next=request.url))

        with get_db_cursor() as cursor:
            cursor.execute("SELECT UserID, Role, IsActive FROM Users WHERE UserID = ?", (admin_id,))
            user_row = cursor.fetchone()

        if not user_row or not user_row[2]: # Active check
            session.pop("admin_user_id", None)
            flash("Your Super Admin account is deactivated or invalid.", "error")
            return redirect(url_for("admin.admin_login"))

        user_role = str(user_row[1] or "").strip()
        if user_role not in ["SuperAdmin", "Admin"]:
            return render_template("403.html"), 403

        return f(*args, **kwargs)

    return decorated_function
