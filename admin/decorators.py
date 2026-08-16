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
        user_id = session.get("user_id")
        if not user_id:
            flash("Super Admin authentication required to access executive console.", "warning")
            return redirect(url_for("admin.admin_login", next=request.url))

        with get_db_cursor() as cursor:
            cursor.execute("SELECT UserID, Role, IsActive FROM Users WHERE UserID = ?", (user_id,))
            user_row = cursor.fetchone()

        if not user_row or not user_row[2]: # Active check
            session.clear()
            flash("Your account is deactivated or invalid.", "error")
            return redirect(url_for("admin.admin_login"))

        user_role = str(user_row[1] or "").strip()
        if user_role not in ["SuperAdmin", "Admin"]:
            return render_template("403.html"), 403

        return f(*args, **kwargs)

    return decorated_function
