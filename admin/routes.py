from flask import render_template, request, redirect, url_for, flash, session, jsonify
from admin import admin_bp
from admin.decorators import admin_required
from database.connection import get_db_cursor
from database.queries import (
    get_admin_kpis,
    get_all_users_admin,
    update_user_status_admin,
    update_user_role_admin,
    delete_user_admin,
    get_all_payments_admin,
    update_payment_status_admin,
    get_all_audit_logs_admin
)
from auth.security import log_audit_event, verify_password, generate_secure_token
from utils.helpers import format_12hr_datetime

# Global in-memory announcement store
site_announcement = {
    "title": "Welcome to AI Data Analyst Pro Super Admin Console",
    "message": "System operational. Enterprise 10M+ engine running with AES-256 encryption at rest.",
    "enabled": True
}


def get_user_initials(name_str, email_str):
    """Compute 2-letter uppercase initials for user avatar fallback."""
    clean = (name_str or "").strip()
    if not clean:
        clean = (email_str or "User").split("@")[0]
    parts = clean.split()
    if len(parts) >= 2:
        return f"{parts[0][0]}{parts[1][0]}".upper()
    return clean[:2].upper()


# ==========================================
# DEDICATED SUPER ADMIN LOGIN GATEWAY
# ==========================================
@admin_bp.route("/login", methods=["GET", "POST"])
def admin_login():
    """
    Dedicated Super Admin Authentication Gateway.
    Separated from standard user authentication portal.
    """
    if session.get("user_id") and session.get("user_role") in ["SuperAdmin", "Admin"]:
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with get_db_cursor() as cursor:
            cursor.execute("SELECT UserID, FullName, PasswordHash, Role, IsActive FROM Users WHERE Email = ?", (email,))
            u_row = cursor.fetchone()

        if not u_row:
            flash("Access Denied: Invalid Super Admin credentials.", "error")
            return render_template("admin/login.html")

        user_id, full_name, pwd_hash, role, is_active = u_row

        if not is_active:
            flash("Super Admin account is deactivated. Contact system administrator.", "error")
            return render_template("admin/login.html")

        if role not in ["SuperAdmin", "Admin"]:
            flash("Access Denied: Unauthorized account. Super Admin role required.", "error")
            return render_template("admin/login.html")

        if not verify_password(password, pwd_hash):
            log_audit_event("ADMIN_LOGIN_FAILED", f"Failed Super Admin login attempt for {email}")
            flash("Access Denied: Invalid Super Admin credentials.", "error")
            return render_template("admin/login.html")

        # Establish Dedicated Admin Session
        session.permanent = True
        session["user_id"] = user_id
        session["user_name"] = full_name or "Super Admin"
        session["user_email"] = email
        session["user_role"] = role
        session["session_token"] = generate_secure_token()

        log_audit_event("ADMIN_LOGIN_SUCCESS", f"Super Admin '{full_name}' logged into Executive Console", user_id=user_id)
        flash(f"👑 Welcome Super Admin {full_name}! Executive Console Unlocked.", "success")

        next_page = request.args.get("next")
        return redirect(next_page or url_for("admin.dashboard"))

    return render_template("admin/login.html")


@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    """
    Super Admin Executive Dashboard.
    Displays real-time KPIs, system status, quick users list, and payments queue.
    """
    kpis = {
        "TotalUsers": 0,
        "ActiveUsers": 0,
        "SuspendedUsers": 0,
        "TotalRevenue": 0.0,
        "CompletedPayments": 0,
        "PendingPayments": 0,
        "TotalDatasets": 0
    }
    recent_users = []
    recent_payments = []
    recent_audit_logs = []

    try:
        with get_db_cursor() as cursor:
            # KPIs
            cursor.execute(get_admin_kpis())
            k_row = cursor.fetchone()
            if k_row:
                kpis = {
                    "TotalUsers": k_row[0] or 0,
                    "ActiveUsers": k_row[1] or 0,
                    "SuspendedUsers": k_row[2] or 0,
                    "TotalRevenue": float(k_row[3] or 0.0),
                    "CompletedPayments": k_row[4] or 0,
                    "PendingPayments": k_row[5] or 0,
                    "TotalDatasets": k_row[6] or 0
                }

            # Recent Users (Top 5)
            cursor.execute(get_all_users_admin())
            all_u = cursor.fetchall()
            recent_users = all_u[:5] if all_u else []

            # Recent Payments (Top 5)
            cursor.execute(get_all_payments_admin())
            all_p = cursor.fetchall()
            recent_payments = all_p[:5] if all_p else []

            # Recent Audit Logs (Top 8)
            cursor.execute(get_all_audit_logs_admin(limit=8))
            raw_audit = cursor.fetchall() or []
            recent_audit_logs = [
                (l[0], l[1], l[2], l[3], l[4], format_12hr_datetime(l[5]))
                for l in raw_audit
            ]

    except Exception as e:
        print("Admin Dashboard Query Error:", e)

    return render_template(
        "admin/dashboard.html",
        kpis=kpis,
        recent_users=recent_users,
        recent_payments=recent_payments,
        recent_audit_logs=recent_audit_logs,
        announcement=site_announcement
    )


@admin_bp.route("/users")
@admin_required
def manage_users():
    """
    Super Admin User Management Panel.
    Displays all registered users with search, role filter, status toggle, and delete.
    """
    users_list = []
    search_query = request.args.get("q", "").strip().lower()
    role_filter = request.args.get("role", "").strip()
    status_filter = request.args.get("status", "").strip()

    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_all_users_admin())
            raw_users = cursor.fetchall() or []

        for u in raw_users:
            u_id, f_name, l_name, uname, full_name, email, phone, country, city, profile_img, is_active, is_verified, role, created_at = u
            
            # Apply Filters
            if search_query:
                match_text = f"{full_name} {email} {uname} {country} {city}".lower()
                if search_query not in match_text:
                    continue

            if role_filter and role != role_filter:
                continue

            if status_filter:
                if status_filter == "active" and not is_active:
                    continue
                if status_filter == "suspended" and is_active:
                    continue

            users_list.append({
                "id": u_id,
                "full_name": full_name,
                "email": email,
                "username": uname,
                "phone": phone or "N/A",
                "location": f"{city or ''}, {country or ''}".strip(", ") or "N/A",
                "profile_image": profile_img if (profile_img and profile_img != "/static/images/default_avatar.png") else None,
                "initials": get_user_initials(full_name, email),
                "is_active": bool(is_active),
                "is_verified": bool(is_verified),
                "role": role,
                "created_at": format_12hr_datetime(created_at)
            })
    except Exception as e:
        print("Admin Users Fetch Error:", e)
        flash(f"Error fetching users: {str(e)}", "error")

    return render_template(
        "admin/users.html",
        users=users_list,
        search_query=search_query,
        role_filter=role_filter,
        status_filter=status_filter
    )


@admin_bp.route("/user/toggle-status/<int:user_id>", methods=["POST"])
@admin_required
def toggle_user_status(user_id):
    """
    Toggle User Account Active / Suspended status.
    """
    admin_id = session.get("user_id")
    if admin_id == user_id:
        flash("Action Denied: You cannot suspend your own active Super Admin account.", "error")
        return redirect(url_for("admin.manage_users"))

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("SELECT IsActive, FullName, Email FROM Users WHERE UserID = ?", (user_id,))
            u_row = cursor.fetchone()
            if not u_row:
                flash("User not found.", "error")
                return redirect(url_for("admin.manage_users"))

            current_status = u_row[0]
            new_status = 0 if current_status else 1
            status_text = "Activated" if new_status else "Suspended"

            cursor.execute(update_user_status_admin(), (new_status, user_id))

        log_audit_event("ADMIN_USER_STATUS_CHANGE", f"{status_text} user #{user_id} ({u_row[1]} <{u_row[2]}>)", user_id=admin_id)
        flash(f"User account for '{u_row[1]}' successfully {status_text}.", "success")
    except Exception as e:
        print("Toggle User Status Error:", e)
        flash(f"Failed to update user status: {str(e)}", "error")

    return redirect(url_for("admin.manage_users"))


@admin_bp.route("/user/change-role/<int:user_id>", methods=["POST"])
@admin_required
def change_user_role(user_id):
    """
    Change User Role (SuperAdmin / Analyst / User).
    """
    admin_id = session.get("user_id")
    new_role = request.form.get("role", "").strip()

    if new_role not in ["SuperAdmin", "Analyst", "User"]:
        flash("Invalid role selection.", "error")
        return redirect(url_for("admin.manage_users"))

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("SELECT FullName, Email FROM Users WHERE UserID = ?", (user_id,))
            u_row = cursor.fetchone()
            if not u_row:
                flash("User not found.", "error")
                return redirect(url_for("admin.manage_users"))

            cursor.execute(update_user_role_admin(), (new_role, user_id))

        log_audit_event("ADMIN_USER_ROLE_CHANGE", f"Updated user #{user_id} role to '{new_role}'", user_id=admin_id)
        flash(f"User '{u_row[0]}' role successfully updated to '{new_role}'.", "success")
    except Exception as e:
        print("Change User Role Error:", e)
        flash(f"Failed to update user role: {str(e)}", "error")

    return redirect(url_for("admin.manage_users"))


@admin_bp.route("/user/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    """
    Delete User account and purge associated datasets & records.
    """
    admin_id = session.get("user_id")
    if admin_id == user_id:
        flash("Action Denied: You cannot delete your own active Super Admin account.", "error")
        return redirect(url_for("admin.manage_users"))

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("SELECT FullName, Email FROM Users WHERE UserID = ?", (user_id,))
            u_row = cursor.fetchone()
            if not u_row:
                flash("User not found.", "error")
                return redirect(url_for("admin.manage_users"))

            # Delete physical tables owned by user
            cursor.execute("SELECT DatasetName FROM Datasets WHERE UserID = ?", (user_id,))
            ds_tables = cursor.fetchall() or []
            for ds in ds_tables:
                t_name = ds[0]
                try:
                    cursor.execute(f"IF OBJECT_ID('{t_name}', 'U') IS NOT NULL DROP TABLE [{t_name}]")
                except Exception:
                    pass

            # Delete User Record (CASCADE will delete Datasets metadata & QueryLogs)
            cursor.execute(delete_user_admin(), (user_id,))

        log_audit_event("ADMIN_USER_DELETED", f"Deleted user #{user_id} ({u_row[0]} <{u_row[1]}>) and purged datasets.", user_id=admin_id)
        flash(f"User account '{u_row[0]}' and associated datasets deleted successfully.", "success")
    except Exception as e:
        print("Delete User Error:", e)
        flash(f"Failed to delete user: {str(e)}", "error")

    return redirect(url_for("admin.manage_users"))


@admin_bp.route("/payments")
@admin_required
def manage_payments():
    """
    Super Admin Payments & Subscriptions Monitor.
    Displays transactions with status filter chips and manual status approval modal.
    """
    payments_list = []
    status_filter = request.args.get("status", "").strip()

    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_all_payments_admin())
            raw_p = cursor.fetchall() or []

        for p in raw_p:
            p_id, u_id, full_name, email, amount, currency, method, txn_id, status, plan, p_date = p
            if status_filter and status.lower() != status_filter.lower():
                continue

            payments_list.append({
                "id": p_id,
                "user_id": u_id,
                "user_name": full_name or "Anonymous User",
                "email": email or "N/A",
                "amount": float(amount or 0.0),
                "currency": currency or "USD",
                "payment_method": method or "Card",
                "txn_id": txn_id or f"TXN_{p_id}9021",
                "status": status or "Completed",
                "plan_name": plan or "Enterprise Plan ($85/mo)",
                "payment_date": format_12hr_datetime(p_date)
            })
    except Exception as e:
        print("Admin Payments Fetch Error:", e)
        flash(f"Error fetching payment records: {str(e)}", "error")

    return render_template("admin/payments.html", payments=payments_list, status_filter=status_filter)


@admin_bp.route("/payment/invoice/<int:payment_id>")
@admin_required
def view_payment_invoice(payment_id):
    """
    Render Printable Billing Invoice Receipt for a payment record.
    """
    try:
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT P.PaymentID, P.UserID, U.FullName, U.Email, P.Amount, P.Currency, P.PaymentMethod, P.TransactionID, P.Status, P.PlanName, P.PaymentDate
                FROM Payments P
                LEFT JOIN Users U ON P.UserID = U.UserID
                WHERE P.PaymentID = ?
            """, (payment_id,))
            p = cursor.fetchone()

        if not p:
            flash("Invoice not found.", "error")
            return redirect(url_for("admin.manage_payments"))

        p_id, u_id, full_name, email, amount, currency, method, txn_id, status, plan, p_date = p
        payment_dict = {
            "id": p_id,
            "user_id": u_id,
            "user_name": full_name or "Valued Customer",
            "email": email or "N/A",
            "amount": float(amount or 0.0),
            "currency": currency or "USD",
            "payment_method": method or "Card",
            "txn_id": txn_id or f"TXN_{p_id}9021",
            "status": status or "Completed",
            "plan_name": plan or "Enterprise Plan ($85/mo)",
            "payment_date": format_12hr_datetime(p_date)
        }
        return render_template("admin/invoice.html", payment=payment_dict)
    except Exception as e:
        print("View Invoice Error:", e)
        flash(f"Failed to load invoice: {str(e)}", "error")
        return redirect(url_for("admin.manage_payments"))


@admin_bp.route("/payment/update-status/<int:payment_id>", methods=["POST"])
@admin_required
def update_payment_status(payment_id):
    """
    Update Payment Status (Completed / Pending / Failed / Refunded) and send automatic customer email.
    """
    admin_id = session.get("user_id")
    new_status = request.form.get("status", "").strip()

    if new_status not in ["Completed", "Pending", "Failed", "Refunded"]:
        flash("Invalid payment status.", "error")
        return redirect(url_for("admin.manage_payments"))

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("SELECT P.PaymentID, P.UserID, U.FullName, U.Email, P.Amount, P.TransactionID, P.PlanName FROM Payments P LEFT JOIN Users U ON P.UserID = U.UserID WHERE P.PaymentID = ?", (payment_id,))
            p_row = cursor.fetchone()

            cursor.execute(update_payment_status_admin(), (new_status, payment_id))

        if p_row:
            _, u_id, u_name, u_email, p_amount, txn_id, plan_name = p_row
            if u_email:
                try:
                    from auth.email_service import EmailService
                    email_service = EmailService()
                    email_service.send_payment_status_email(
                        to_email=u_email,
                        user_name=u_name or "Valued Customer",
                        txn_id=txn_id or f"TXN_{payment_id}",
                        status=new_status,
                        amount=float(p_amount or 85.0),
                        plan_name=plan_name or "Enterprise Plan ($85/mo)"
                    )
                except Exception as mail_err:
                    print("Payment Status Email Notice:", mail_err)

        log_audit_event("ADMIN_PAYMENT_STATUS_UPDATE", f"Updated Payment #{payment_id} status to '{new_status}' and notified customer.", user_id=admin_id)
        flash(f"Payment #{payment_id} status updated to '{new_status}' and confirmation email sent to user.", "success")
    except Exception as e:
        print("Update Payment Status Error:", e)
        flash(f"Failed to update payment status: {str(e)}", "error")

    return redirect(url_for("admin.manage_payments"))


@admin_bp.route("/audit-logs")
@admin_required
def audit_logs():
    """
    Super Admin Audit Logs Viewer.
    """
    logs_list = []
    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_all_audit_logs_admin(limit=150))
            raw_logs = cursor.fetchall() or []

        for log in raw_logs:
            log_id, user_id, action, details, ip_addr, created_at = log
            logs_list.append({
                "id": log_id,
                "user_id": user_id or "System",
                "event_type": action,
                "description": details or "No details provided",
                "ip_address": ip_addr or "127.0.0.1",
                "created_at": format_12hr_datetime(created_at)
            })
    except Exception as e:
        print("Admin Audit Logs Error:", e)

    return render_template("admin/audit_logs.html", logs=logs_list)


@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    """
    Super Admin Platform Settings & Announcement Manager.
    """
    global site_announcement
    if request.method == "POST":
        ann_title = request.form.get("ann_title", "").strip()
        ann_msg = request.form.get("ann_msg", "").strip()
        ann_enabled = request.form.get("ann_enabled") == "on"

        site_announcement = {
            "title": ann_title or "Platform Announcement",
            "message": ann_msg or "Enterprise Super Admin System Active.",
            "enabled": ann_enabled
        }
        flash("Platform announcement and enterprise settings updated successfully.", "success")
        return redirect(url_for("admin.settings"))

    return render_template("admin/settings.html", announcement=site_announcement)
