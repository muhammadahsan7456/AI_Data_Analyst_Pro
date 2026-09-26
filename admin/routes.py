import time
import secrets
from datetime import datetime, timedelta
from flask import render_template, request, redirect, url_for, flash, session, Response
from markupsafe import escape
from admin import admin_bp
from admin.decorators import admin_required
from database.connection import get_db_cursor
from database.queries import (
    get_admin_kpis,
    get_all_users_admin,
    get_expiring_users_admin,
    update_user_status_admin,
    update_user_role_admin,
    delete_user_admin,
    get_all_payments_admin,
    update_payment_status_admin,
    get_all_audit_logs_admin,
    get_all_contact_messages,
    update_contact_message_status,
    delete_contact_message,
    save_contact_message_reply,
    get_contact_message_by_id,
    increment_failed_login,
    reset_failed_login,
    get_site_announcement,
    upsert_site_announcement,
    insert_site_announcement,
    get_monthly_revenue_trend,
    get_monthly_signups_trend,
    get_per_user_storage_admin,
    update_payment_settings,
    activate_user_new_subscription
)
from auth.security import log_audit_event, verify_password, generate_secure_token
from utils.helpers import format_12hr_datetime


def fetch_site_announcement():
    """
    Reads the single-row SiteAnnouncement table, persisted in the database instead of
    the process's memory - previously an in-memory dict here meant the announcement
    silently reset to its hardcoded default every time the server restarted.
    """
    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_site_announcement())
            row = cursor.fetchone()
        if row:
            return {"title": row[0], "message": row[1], "enabled": bool(row[2])}
    except Exception as e:
        print("Fetch Site Announcement Error:", e)
    return {"title": "Platform Announcement", "message": "", "enabled": False}


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
    if session.get("admin_user_id") or (session.get("user_id") and session.get("user_role") in ["SuperAdmin", "Admin"]):
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with get_db_cursor() as cursor:
            cursor.execute(
                "SELECT UserID, FullName, PasswordHash, Role, IsActive, FailedLoginAttempts, LockoutUntil FROM Users WHERE Email = ?",
                (email,)
            )
            u_row = cursor.fetchone()

        if not u_row:
            flash("Access Denied: Invalid Super Admin credentials.", "error")
            return render_template("admin/login.html")

        user_id, full_name, pwd_hash, role, is_active, failed_attempts, lockout_until = u_row

        if not is_active:
            flash("Super Admin account is deactivated. Contact system administrator.", "error")
            return render_template("admin/login.html")

        if role not in ["SuperAdmin", "Admin"]:
            flash("Access Denied: Unauthorized account. Super Admin role required.", "error")
            return render_template("admin/login.html")

        # Same 5-attempts / 15-minute lockout policy already enforced for regular user
        # login (auth/routes.py) - reused here via the same Users.LockoutUntil column,
        # since brute-forcing the account that controls billing/user data is the single
        # highest-value target on the whole site.
        if lockout_until:
            try:
                lockout_dt = datetime.fromisoformat(str(lockout_until)) if isinstance(lockout_until, str) else lockout_until
                if datetime.now() < lockout_dt:
                    log_audit_event("ADMIN_LOGIN_LOCKED", f"Login blocked (account locked) for {email}")
                    flash("Account temporarily locked due to multiple failed login attempts. Please try again in a few minutes.", "error")
                    return render_template("admin/login.html")
            except Exception:
                pass

        if not verify_password(password, pwd_hash):
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(increment_failed_login(), (user_id,))
            log_audit_event("ADMIN_LOGIN_FAILED", f"Failed Super Admin login attempt for {email}")
            flash("Access Denied: Invalid Super Admin credentials.", "error")
            return render_template("admin/login.html")

        with get_db_cursor(commit=True) as cursor:
            cursor.execute(reset_failed_login(), (user_id,))

        # Password correct - now require a 6-digit email OTP (second factor) before
        # establishing the actual admin session, so a leaked/guessed password alone
        # can no longer grant Super Admin access.
        otp_code = f"{secrets.randbelow(900000) + 100000}"
        session["pending_admin_otp"] = {
            "user_id": user_id,
            "full_name": full_name or "Super Admin",
            "email": email,
            "role": role,
            "otp_code": otp_code,
            "issued_at": datetime.now().isoformat(),
            "attempts": 0,
            "next": request.args.get("next") or request.form.get("next")
        }

        from auth.email_service import EmailService
        EmailService().send_admin_login_otp_email(
            to_email=email,
            user_name=full_name or "Super Admin",
            otp_code=otp_code,
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
        )

        log_audit_event("ADMIN_LOGIN_OTP_SENT", f"Password verified, OTP sent to {email} for Super Admin login", user_id=user_id)
        return redirect(url_for("admin.admin_verify_otp"))

    return render_template("admin/login.html")


@admin_bp.route("/verify-otp", methods=["GET", "POST"])
def admin_verify_otp():
    """
    Second-factor step of Super Admin login: verifies the 6-digit email code issued by
    admin_login() before the actual admin session is established. Mirrors the same
    expiry/attempt-limit pattern used for the regular signup OTP flow (auth/routes.py).
    """
    pending = session.get("pending_admin_otp")
    if not pending:
        flash("No pending Super Admin verification session found. Please log in again.", "warning")
        return redirect(url_for("admin.admin_login"))

    if request.method == "POST":
        input_code = request.form.get("otp_code", "").strip()
        expected_code = str(pending.get("otp_code", "")).strip()
        user_id = pending.get("user_id")

        try:
            issued_at = datetime.fromisoformat(pending.get("issued_at"))
            expired = (datetime.now() - issued_at).total_seconds() > 600
        except Exception:
            expired = True

        if expired:
            session.pop("pending_admin_otp", None)
            flash("Verification code expired. Please log in again to receive a new code.", "error")
            return redirect(url_for("admin.admin_login"))

        attempts = pending.get("attempts", 0)
        if attempts >= 5:
            session.pop("pending_admin_otp", None)
            log_audit_event("ADMIN_OTP_MAX_ATTEMPTS", f"Max OTP attempts exceeded for {pending.get('email')}")
            flash("Maximum verification attempts exceeded. Please log in again to receive a new code.", "error")
            return redirect(url_for("admin.admin_login"))

        if input_code == expected_code:
            session.pop("pending_admin_otp", None)
            session.permanent = True
            session["admin_user_id"] = user_id
            session["admin_user_name"] = pending.get("full_name")
            session["admin_user_email"] = pending.get("email")
            session["admin_user_role"] = pending.get("role")
            session["admin_session_token"] = generate_secure_token()
            session["admin_last_activity"] = int(time.time())

            log_audit_event("ADMIN_LOGIN_SUCCESS", f"Super Admin '{pending.get('full_name')}' logged into Executive Console (2FA verified)", user_id=user_id)
            flash(f"👑 Welcome Super Admin {escape(pending.get('full_name') or '')}! Executive Console Unlocked.", "success")
            return redirect(pending.get("next") or url_for("admin.dashboard"))
        else:
            attempts += 1
            remaining = 5 - attempts
            log_audit_event("ADMIN_OTP_FAILED", f"Invalid OTP attempt for {pending.get('email')} ({remaining} remaining)")

            if remaining <= 0:
                session.pop("pending_admin_otp", None)
                log_audit_event("ADMIN_OTP_MAX_ATTEMPTS", f"Max OTP attempts exceeded for {pending.get('email')}")
                flash("Maximum verification attempts exceeded. Please log in again to receive a new code.", "error")
                return redirect(url_for("admin.admin_login"))

            pending["attempts"] = attempts
            session["pending_admin_otp"] = pending
            flash(f"❌ Invalid verification code. {remaining} attempt(s) remaining.", "error")

    return render_template("admin/verify_otp.html", email=pending.get("email"))


@admin_bp.route("/logout")
def admin_logout():
    admin_id = session.get("admin_user_id")
    admin_name = session.get("admin_user_name", "Super Admin")
    if admin_id:
        log_audit_event("ADMIN_LOGOUT", f"Super Admin '{admin_name}' logged out.", user_id=admin_id)

    for k in ["admin_user_id", "admin_user_name", "admin_user_email", "admin_user_role", "admin_session_token", "admin_last_activity"]:
        session.pop(k, None)

    flash("👋 Super Admin logged out successfully.", "info")
    return redirect(url_for("admin.admin_login"))


@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    """
    Super Admin Executive Dashboard.
    Displays real-time KPIs, system status, quick users list, and payments queue.
    """
    # Revenue/Signups trend date range - defaults to the last 6 months, but the
    # Super Admin can pick any custom range via the date inputs above the charts.
    end_date_str = request.args.get("trend_end", "").strip()
    start_date_str = request.args.get("trend_start", "").strip()
    try:
        trend_end = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else datetime.now()
    except ValueError:
        trend_end = datetime.now()
    try:
        trend_start = datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else (trend_end - timedelta(days=182))
    except ValueError:
        trend_start = trend_end - timedelta(days=182)

    kpis = {
        "TotalUsers": 0,
        "ActiveUsers": 0,
        "SuspendedUsers": 0,
        "TotalRevenue": 0.0,
        "CompletedPayments": 0,
        "PendingPayments": 0,
        "TotalDatasets": 0,
        "ExpiringSoonUsers": 0,
        "ExpiredUsers": 0
    }
    recent_users = []
    recent_payments = []
    recent_audit_logs = []
    expiring_users = []
    revenue_trend = []
    signups_trend = []

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
                    "TotalDatasets": k_row[6] or 0,
                    "ExpiringSoonUsers": k_row[7] if len(k_row) > 7 else 0,
                    "ExpiredUsers": k_row[8] if len(k_row) > 8 else 0
                }

            # Users whose subscription is expiring soon or already expired,
            # so the Super Admin proactively sees who needs a renewal follow-up
            # instead of having to notice it while browsing the full Users list.
            cursor.execute(get_expiring_users_admin(limit=8))
            raw_exp = cursor.fetchall() or []
            for eu in raw_exp:
                end_dt = eu[4]
                days_left = None
                try:
                    from datetime import datetime as _dt
                    if end_dt:
                        days_left = (end_dt - _dt.now()).days
                except Exception:
                    days_left = None
                expiring_users.append({
                    "user_id": eu[0],
                    "full_name": eu[1] or "Unnamed User",
                    "email": eu[2],
                    "status": eu[3],
                    "end_date": format_12hr_datetime(end_dt) if end_dt else "N/A",
                    "days_left": days_left
                })

            # Recent Users (Top 5)
            cursor.execute(get_all_users_admin())
            raw_u = cursor.fetchall() or []
            recent_users = []
            for u in raw_u[:5]:
                recent_users.append({
                    "full_name": u[4] or u[3],
                    "email": u[5],
                    "role": u[12],
                    "is_active": u[10],
                    "created_at": format_12hr_datetime(u[13])
                })

            # Recent Payments (Top 5)
            cursor.execute(get_all_payments_admin())
            raw_p = cursor.fetchall() or []
            recent_payments = []
            for p in raw_p[:5]:
                recent_payments.append({
                    "user_name": p[2] or f"User #{p[1]}",
                    "amount": float(p[4] or 0.0),
                    "payment_method": p[6],
                    "status": p[8],
                    "payment_date": format_12hr_datetime(p[10])
                })

            # Recent Audit Logs (Top 8)
            cursor.execute(get_all_audit_logs_admin(limit=8))
            raw_audit = cursor.fetchall() or []
            recent_audit_logs = [
                (l[0], l[1], l[2], l[3], l[4], format_12hr_datetime(l[5]))
                for l in raw_audit
            ]

            # Revenue & signups trend for the dashboard chart, over whatever date
            # range was picked (defaults to the last 6 months).
            cursor.execute(get_monthly_revenue_trend(), (trend_start, trend_end))
            revenue_trend = [{"month": r[0], "revenue": float(r[1] or 0)} for r in cursor.fetchall()]

            cursor.execute(get_monthly_signups_trend(), (trend_start, trend_end))
            signups_trend = [{"month": r[0], "signups": r[1] or 0} for r in cursor.fetchall()]

    except Exception as e:
        print("Admin Dashboard Query Error:", e)

    announcement = fetch_site_announcement()
    return render_template(
        "admin/dashboard.html",
        kpis=kpis,
        recent_users=recent_users,
        recent_payments=recent_payments,
        recent_audit_logs=recent_audit_logs,
        expiring_users=expiring_users,
        announcement=announcement,
        system_announcement=announcement["message"] if announcement.get("enabled") else None,
        revenue_trend=revenue_trend,
        signups_trend=signups_trend,
        trend_start=trend_start.strftime("%Y-%m-%d"),
        trend_end=trend_end.strftime("%Y-%m-%d")
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

            # Per-user dataset count & storage usage, so the Super Admin can see who is
            # uploading the most data without having to open each account individually.
            cursor.execute(get_per_user_storage_admin())
            storage_by_user = {row[0]: {"dataset_count": row[3], "storage_kb": float(row[4] or 0), "total_rows": row[5] or 0} for row in cursor.fetchall()}

        for u in raw_users:
            u_id = u[0]
            f_name, l_name, uname, full_name, email = u[1], u[2], u[3], u[4], u[5]
            phone, country, city, profile_img = u[6], u[7], u[8], u[9]
            is_active, is_verified, role, created_at = u[10], u[11], u[12], u[13]
            last_ip = u[14] if len(u) > 14 else "127.0.0.1"
            sub_status = u[15] if len(u) > 15 else "active"
            sub_end = u[16] if len(u) > 16 else None
            
            # Apply Filters
            if search_query:
                match_text = f"{full_name} {email} {uname} {country} {city} {last_ip}".lower()
                if search_query not in match_text:
                    continue

            if role_filter and role != role_filter:
                continue

            if status_filter:
                if status_filter == "active" and (not is_active or sub_status not in ("active", "expiring_soon")):
                    continue
                if status_filter == "suspended" and sub_status != "suspended":
                    continue
                if status_filter == "expired" and sub_status != "expired":
                    continue
                if status_filter == "pending_approval" and sub_status != "pending_approval":
                    continue
                if status_filter == "rejected" and sub_status != "rejected":
                    continue

            sub_end_formatted = format_12hr_datetime(sub_end) if sub_end else "N/A"
            usage = storage_by_user.get(u_id, {"dataset_count": 0, "storage_kb": 0.0, "total_rows": 0})
            storage_kb = usage["storage_kb"]
            storage_display = f"{storage_kb / 1024:.1f} MB" if storage_kb >= 1024 else f"{storage_kb:.0f} KB"

            users_list.append({
                "id": u_id,
                "full_name": full_name,
                "email": email,
                "username": uname,
                "phone": phone or "N/A",
                "location": f"{city or ''}, {country or ''}".strip(", ") or "N/A",
                "profile_image": profile_img if (profile_img and profile_img != "/static/images/default_avatar.png") else None,
                "initials": get_user_initials(full_name, email),
                "is_active": bool(is_active) and sub_status != "suspended",
                "subscription_status": sub_status,
                "subscription_end": sub_end_formatted,
                "is_verified": bool(is_verified),
                "role": role,
                "ip_address": last_ip or "127.0.0.1",
                "created_at": format_12hr_datetime(created_at),
                "dataset_count": usage["dataset_count"],
                "storage_display": storage_display,
                "total_rows": usage["total_rows"]
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


@admin_bp.route("/users/export/<format_type>")
@admin_required
def export_users_admin(format_type):
    """
    Export the full registered-users list as CSV or Excel for accounting/record-keeping.
    """
    import pandas as pd
    from utils.exporters import export_to_csv, export_to_excel

    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_all_users_admin())
            rows = [tuple(r) for r in (cursor.fetchall() or [])]

        df = pd.DataFrame(rows, columns=[
            "UserID", "FirstName", "LastName", "Username", "FullName", "Email", "PhoneNumber",
            "Country", "City", "ProfileImage", "IsActive", "IsVerified", "Role", "CreatedAt",
            "LastIPAddress", "SubscriptionStatus", "SubscriptionEndDate"
        ]).drop(columns=["ProfileImage"])

        if format_type == "excel":
            data = export_to_excel(df, dataset_name="Users")
            mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = "users_export.xlsx"
        else:
            data = export_to_csv(df)
            mimetype = "text/csv"
            filename = "users_export.csv"

        log_audit_event("ADMIN_EXPORT_USERS", f"Exported users list as {format_type}", user_id=session.get("user_id"))
        return Response(data, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={filename}"})
    except Exception as e:
        print("Export Users Error:", e)
        flash(f"Failed to export users: {str(e)}", "error")
        return redirect(url_for("admin.manage_users"))


@admin_bp.route("/user/toggle-status/<int:user_id>", methods=["POST"])
@admin_required
def toggle_user_status(user_id):
    """
    Toggle User Account Active / Suspended status.
    """
    admin_id = session.get("admin_user_id") or session.get("user_id")
    if admin_id == user_id:
        flash("Action Denied: You cannot suspend your own active Super Admin account.", "error")
        return redirect(url_for("admin.manage_users"))

    manual_reason = request.form.get("reason", "").strip()

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("SELECT IsActive, FullName, Email FROM Users WHERE UserID = ?", (user_id,))
            u_row = cursor.fetchone()
            if not u_row:
                flash("User not found.", "error")
                return redirect(url_for("admin.manage_users"))

            current_status = u_row[0]
            reactivating = not current_status
            status_text = "Activated" if reactivating else "Suspended"

            if reactivating:
                cursor.execute(activate_user_new_subscription(), (user_id,))
            else:
                cursor.execute(
                    "UPDATE Users SET IsActive = 0, SubscriptionStatus = 'suspended', SuspensionReason = ? WHERE UserID = ?",
                    (manual_reason or "Suspended by Super Admin.", user_id)
                )

        if u_row[2]:
            try:
                from auth.email_service import EmailService
                es = EmailService()
                if reactivating:
                    with get_db_cursor() as cursor:
                        cursor.execute("SELECT SubscriptionEndDate FROM Users WHERE UserID = ?", (user_id,))
                        end_dt = cursor.fetchone()[0]
                    es.send_subscription_renewed_email(u_row[2], u_row[1], datetime.now().strftime("%d %b %Y"), end_dt.strftime("%d %b %Y") if end_dt else "N/A")
                else:
                    es.send_subscription_suspended_email(u_row[2], u_row[1])
            except Exception as mail_err:
                print("Manual Status Change Email Notice:", mail_err)

        log_audit_event("ADMIN_USER_STATUS_CHANGE", f"{status_text} user #{user_id} ({u_row[1]} <{u_row[2]}>)" + (f" - reason: {manual_reason}" if manual_reason else ""), user_id=admin_id)
        flash(f"User account for '{escape(u_row[1])}' successfully {status_text}.", "success")
    except Exception as e:
        print("Toggle User Status Error:", e)
        flash(f"Failed to update user status: {str(e)}", "error")

    return redirect(url_for("admin.manage_users"))


@admin_bp.route("/user/extend-subscription/<int:user_id>", methods=["POST"])
@admin_required
def extend_user_subscription(user_id):
    """
    Super Admin Action: extend a user's plan by one calendar month and re-activate them.
    """
    admin_id = session.get("admin_user_id") or session.get("user_id")
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(activate_user_new_subscription(), (user_id,))
            cursor.execute("SELECT FullName, Email, SubscriptionEndDate FROM Users WHERE UserID = ?", (user_id,))
            u_row = cursor.fetchone()

        if u_row:
            end_label = u_row[2].strftime("%d %b %Y") if u_row[2] else "N/A"
            log_audit_event("ADMIN_EXTEND_SUBSCRIPTION", f"Extended subscription (+1 calendar month) and activated user #{user_id} ({u_row[0]} <{u_row[1]}>)", user_id=admin_id)
            flash(f"Subscription for '{escape(u_row[0])}' successfully extended to {end_label} and account activated!", "success")
            if u_row[1]:
                try:
                    from auth.email_service import EmailService
                    EmailService().send_subscription_renewed_email(u_row[1], u_row[0], datetime.now().strftime("%d %b %Y"), end_label)
                except Exception as mail_err:
                    print("Extend Subscription Email Notice:", mail_err)
    except Exception as e:
        print("Extend Subscription Error:", e)
        flash(f"Failed to extend subscription: {str(e)}", "error")

    return redirect(url_for("admin.manage_users"))


@admin_bp.route("/user/change-role/<int:user_id>", methods=["POST"])
@admin_required
def change_user_role(user_id):
    """
    Change User Role (SuperAdmin / Analyst / User).
    """
    admin_id = session.get("admin_user_id") or session.get("user_id")
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
        flash(f"User '{escape(u_row[0])}' role successfully updated to '{new_role}'.", "success")
    except Exception as e:
        print("Change User Role Error:", e)
        flash(f"Failed to update user role: {str(e)}", "error")

    return redirect(url_for("admin.manage_users"))


@admin_bp.route("/user/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    """
    Delete User account and purge all associated datasets, query history, audit logs, and payment records.
    """
    admin_id = session.get("admin_user_id") or session.get("user_id")
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

            # 1. Drop physical dataset SQL tables owned by user
            from database.connection import is_safe_identifier
            cursor.execute("SELECT DatasetName FROM Datasets WHERE UserID = ?", (user_id,))
            ds_tables = cursor.fetchall() or []
            for ds in ds_tables:
                t_candidate = ds[0]
                # DatasetName should always be alnum/underscore (produced by
                # clean_table_name()), but this value is being interpolated directly into
                # a DROP TABLE statement - validating it here too is defense-in-depth in
                # case that invariant is ever broken by a future code path or a manual
                # DB edit, rather than trusting it purely because of where it came from.
                if t_candidate and is_safe_identifier(t_candidate):
                    try:
                        cursor.execute(f"IF OBJECT_ID('{t_candidate}', 'U') IS NOT NULL DROP TABLE [{t_candidate}]")
                    except Exception:
                        pass

            # 2. Delete child records to satisfy Foreign Key constraints
            try:
                cursor.execute("DELETE FROM QueryHistory WHERE UserID = ?", (user_id,))
            except Exception:
                pass
            try:
                cursor.execute("DELETE FROM AIConversations WHERE UserID = ?", (user_id,))
            except Exception:
                pass
            try:
                cursor.execute("DELETE FROM AuditLogs WHERE UserID = ?", (user_id,))
            except Exception:
                pass
            try:
                cursor.execute("DELETE FROM Payments WHERE UserID = ?", (user_id,))
            except Exception:
                pass
            try:
                cursor.execute("DELETE FROM Datasets WHERE UserID = ?", (user_id,))
            except Exception:
                pass

            # 3. Finally delete User Record
            cursor.execute("DELETE FROM Users WHERE UserID = ?", (user_id,))

        log_audit_event("ADMIN_USER_DELETED", f"Deleted user #{user_id} ({u_row[0]} <{u_row[1]}>) and purged datasets & history.", user_id=admin_id)
        flash(f"User account '{escape(u_row[0])}' and all associated datasets & history records deleted successfully.", "success")
    except Exception as e:
        print("Delete User Error:", e)
        flash(f"Failed to delete user: {str(e)}", "error")

    return redirect(url_for("admin.manage_users"))


@admin_bp.route("/payments")
@admin_required
def manage_payments():
    """
    Super Admin Payments & Subscriptions Monitor with Advanced Filters:
    - Search Query
    - Bank / Payment Method (EasyPaisa, NayaPay, JazzCash, Meezan, UBL, HBL, etc.)
    - Status Filter (Completed, Pending_Review, Failed, Refunded)
    - Date Range (Start Date, End Date)
    - Sort Order (Newest First vs Oldest First)
    """
    payments_list = []
    search_query = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    method_filter = request.args.get("method", "").strip()
    sort_order = request.args.get("sort", "newest").strip().lower()
    start_date_str = request.args.get("start_date", "").strip()
    end_date_str = request.args.get("end_date", "").strip()

    order_by = "ASC" if sort_order == "oldest" else "DESC"

    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_all_payments_admin(order_by=order_by))
            raw_p = cursor.fetchall() or []

        for p in raw_p:
            p_id, u_id, full_name, email, amount, currency, method, txn_id, status, plan, p_date, screenshot_path = p[0:12]
            payment_type = p[12] if len(p) > 12 else "New"
            rejection_reason = p[13] if len(p) > 13 else None

            # 1. Search Query Filter
            if search_query:
                match_txt = f"{full_name} {email} {txn_id} {method} {plan}".lower()
                if search_query.lower() not in match_txt:
                    continue

            # 2. Status Filter
            if status_filter:
                if status_filter.lower() == "pending" and status.lower() not in ["pending", "pending_review"]:
                    continue
                elif status_filter.lower() != "pending" and status.lower() != status_filter.lower():
                    continue

            # 3. Method / Bank Filter
            if method_filter:
                if method_filter.lower() not in (method or "").lower():
                    continue

            # 4. Date Range Filter
            if start_date_str or end_date_str:
                p_dt_str = str(p_date)[:10] if p_date else ""
                if start_date_str and p_dt_str < start_date_str:
                    continue
                if end_date_str and p_dt_str > end_date_str:
                    continue

            payments_list.append({
                "id": p_id,
                "user_id": u_id,
                "user_name": full_name or "Anonymous User",
                "email": email or "N/A",
                "amount": float(amount or 0.0),
                "currency": currency or "PKR",
                "payment_method": method or "Card",
                "txn_id": txn_id or f"TXN_{p_id}9021",
                "status": status or "Completed",
                "plan_name": plan or "Enterprise Monthly Plan (PKR 25,000/mo)",
                "payment_date": format_12hr_datetime(p_date),
                "raw_date": str(p_date)[:10],
                "screenshot_path": screenshot_path,
                "payment_type": payment_type,
                "rejection_reason": rejection_reason
            })
    except Exception as e:
        print("Admin Payments Fetch Error:", e)
        flash(f"Error fetching payment records: {str(e)}", "error")

    return render_template(
        "admin/payments.html",
        payments=payments_list,
        search_query=search_query,
        status_filter=status_filter,
        method_filter=method_filter,
        sort_order=sort_order,
        start_date=start_date_str,
        end_date=end_date_str
    )


@admin_bp.route("/payments/export/<format_type>")
@admin_required
def export_payments_admin(format_type):
    """
    Export the full payments/revenue ledger as CSV or Excel for accounting/tax records.
    """
    import pandas as pd
    from utils.exporters import export_to_csv, export_to_excel

    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_all_payments_admin())
            rows = [tuple(r) for r in (cursor.fetchall() or [])]

        df = pd.DataFrame(rows, columns=[
            "PaymentID", "UserID", "FullName", "Email", "Amount", "Currency", "PaymentMethod",
            "TransactionID", "Status", "PlanName", "PaymentDate", "ScreenshotPath"
        ]).drop(columns=["ScreenshotPath"])

        if format_type == "excel":
            data = export_to_excel(df, dataset_name="Payments")
            mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = "payments_export.xlsx"
        else:
            data = export_to_csv(df)
            mimetype = "text/csv"
            filename = "payments_export.csv"

        log_audit_event("ADMIN_EXPORT_PAYMENTS", f"Exported payments ledger as {format_type}", user_id=session.get("user_id"))
        return Response(data, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={filename}"})
    except Exception as e:
        print("Export Payments Error:", e)
        flash(f"Failed to export payments: {str(e)}", "error")
        return redirect(url_for("admin.manage_payments"))


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
            "currency": currency or "PKR",
            "payment_method": method or "Easypaisa",
            "txn_id": txn_id or f"TXN_{p_id}9021",
            "status": status or "Completed",
            "plan_name": plan or "Enterprise Plan (PKR 25,000/mo)",
            "payment_date": format_12hr_datetime(p_date)
        }
        return render_template("admin/invoice.html", payment=payment_dict)
    except Exception as e:
        print("View Invoice Error:", e)
        flash(f"Failed to load invoice: {str(e)}", "error")
        return redirect(url_for("admin.manage_payments"))


@admin_bp.route("/payment/invoice/<int:payment_id>/download")
@admin_required
def download_payment_invoice(payment_id):
    """
    Generate a real PDF file for this invoice and send it as a download - separate from
    the on-screen invoice page's Print button, which only opens the browser's print
    dialog and never actually produces a saved file.
    """
    from utils.exporters import export_invoice_to_pdf

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
            "currency": currency or "PKR",
            "payment_method": method or "Easypaisa",
            "txn_id": txn_id or f"TXN_{p_id}9021",
            "status": status or "Completed",
            "plan_name": plan or "Enterprise Plan (PKR 25,000/mo)",
            "payment_date": format_12hr_datetime(p_date)
        }
        pdf_bytes = export_invoice_to_pdf(payment_dict)
        filename = f"Invoice_{payment_dict['txn_id']}.pdf"
        log_audit_event("ADMIN_DOWNLOAD_INVOICE", f"Downloaded invoice PDF for PaymentID {payment_id}", user_id=session.get("admin_user_id") or session.get("user_id"))
        return Response(pdf_bytes, mimetype="application/pdf", headers={"Content-Disposition": f"attachment; filename={filename}"})
    except Exception as e:
        print("Download Invoice Error:", e)
        flash(f"Failed to generate invoice PDF: {str(e)}", "error")
        return redirect(url_for("admin.manage_payments"))


@admin_bp.route("/payment/update-status/<int:payment_id>", methods=["POST"])
@admin_required
def update_payment_status(payment_id):
    """
    Approve or reject a payment submission.
    - Approve: activates the user (fresh calendar month if they were pending/suspended/
      rejected, or extended from their current end date if they renewed before expiry),
      then emails + in-app notifies them.
    - Reject: requires a reason, marks the user 'rejected' so they see it and can
      re-submit, then emails + in-app notifies them with that reason.
    Plain status changes (Pending_Review/Refunded) are also supported for admin
    record-keeping but don't touch the user's access state.
    """
    admin_id = session.get("admin_user_id") or session.get("user_id")
    new_status = request.form.get("status", "").strip()
    reason = request.form.get("reason", "").strip()

    if new_status not in ["Completed", "Pending", "Pending_Review", "Rejected", "Failed", "Refunded"]:
        flash("Invalid payment status.", "error")
        return redirect(url_for("admin.manage_payments"))

    if new_status in ("Rejected", "Failed") and not reason:
        flash("Please provide a reason for rejecting this payment.", "error")
        return redirect(url_for("admin.manage_payments"))

    try:
        from database.queries import approve_payment_admin, reject_payment_admin, activate_user_new_subscription, extend_user_subscription_one_month, set_user_rejected, create_ai_notification

        with get_db_cursor() as cursor:
            cursor.execute(
                "SELECT P.PaymentID, P.UserID, U.FullName, U.Email, P.Amount, P.TransactionID, P.PlanName, U.SubscriptionStatus, U.SubscriptionEndDate FROM Payments P LEFT JOIN Users U ON P.UserID = U.UserID WHERE P.PaymentID = ?",
                (payment_id,)
            )
            p_row = cursor.fetchone()

        if not p_row:
            flash("Payment not found.", "error")
            return redirect(url_for("admin.manage_payments"))

        _, u_id, u_name, u_email, p_amount, txn_id, plan_name, cur_sub_status, cur_sub_end = p_row
        p_amount = float(p_amount or 0.0)

        if new_status == "Completed":
            if p_amount < 25000.0:
                flash("Approval Blocked: Payment amount is below the required PKR 25,000 threshold. Submissions under PKR 25,000 cannot activate subscription access.", "error")
                return redirect(url_for("admin.manage_payments"))

            # A user renewing BEFORE their current period expires keeps their remaining
            # days (extends from the existing end date); everyone else (new signup,
            # already-suspended, or previously-rejected) starts a fresh month from now.
            renewing_early = (
                cur_sub_status == "active" and cur_sub_end is not None
                and cur_sub_end > datetime.now()
            )

            with get_db_cursor(commit=True) as cursor:
                cursor.execute(approve_payment_admin(), (admin_id, payment_id))
                if renewing_early:
                    cursor.execute(extend_user_subscription_one_month(), (u_id,))
                else:
                    cursor.execute(activate_user_new_subscription(), (u_id,))
                cursor.execute("UPDATE Users SET IsVerified = 1 WHERE UserID = ?", (u_id,))
                cursor.execute("SELECT SubscriptionEndDate FROM Users WHERE UserID = ?", (u_id,))
                new_end_date = cursor.fetchone()[0]

            start_label = datetime.now().strftime("%d %b %Y")
            end_label = new_end_date.strftime("%d %b %Y") if new_end_date else "N/A"

            if u_email:
                try:
                    from auth.email_service import EmailService
                    es = EmailService()
                    if renewing_early or cur_sub_status == "suspended":
                        es.send_subscription_renewed_email(u_email, u_name or "Valued Customer", start_label, end_label)
                    else:
                        es.send_payment_approved_email(u_email, u_name or "Valued Customer", start_label, end_label)
                except Exception as mail_err:
                    print("Payment Approval Email Notice:", mail_err)

            try:
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute(
                        create_ai_notification(),
                        (u_id, "payment_approved", "Dashboard Activated!",
                         f"Your payment has been approved. Your dashboard is active from {start_label} to {end_label}.", None)
                    )
            except Exception:
                pass

            flash(f"Payment #{payment_id} approved. {escape(u_name or 'User')}'s dashboard is now active until {end_label}.", "success")

        elif new_status in ("Rejected", "Failed"):
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(reject_payment_admin(), (reason, admin_id, payment_id))
                cursor.execute(set_user_rejected(), (reason, u_id))

            if u_email:
                try:
                    from auth.email_service import EmailService
                    EmailService().send_payment_rejected_email(u_email, u_name or "Valued Customer", reason)
                except Exception as mail_err:
                    print("Payment Rejection Email Notice:", mail_err)

            try:
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute(
                        create_ai_notification(),
                        (u_id, "payment_rejected", "Payment Verification Issue",
                         f"Your payment could not be approved. Reason: {reason}. Please upload a new screenshot to try again.", None)
                    )
            except Exception:
                pass

            flash(f"Payment #{payment_id} rejected and {escape(u_name or 'User')} has been notified with the reason.", "success")

        else:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(update_payment_status_admin(), (new_status, payment_id))
            flash(f"Payment #{payment_id} status updated to '{new_status}'.", "success")

        log_audit_event("ADMIN_PAYMENT_STATUS_UPDATE", f"Updated Payment #{payment_id} status to '{new_status}'" + (f" (reason: {reason})" if reason else ""), user_id=admin_id)
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
    Super Admin Platform Settings & Announcement Manager. Persisted in the
    SiteAnnouncement table so it survives server restarts (previously an
    in-memory dict here silently reset to its hardcoded default on every restart).
    """
    admin_id = session.get("admin_user_id") or session.get("user_id")
    if request.method == "POST":
        ann_title = request.form.get("ann_title", "").strip() or "Platform Announcement"
        ann_msg = request.form.get("ann_msg", "").strip() or "Enterprise Super Admin System Active."
        ann_enabled = request.form.get("ann_enabled") == "on"

        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(get_site_announcement())
                exists = cursor.fetchone()
                if exists:
                    cursor.execute(upsert_site_announcement(), (ann_title, ann_msg, ann_enabled))
                else:
                    cursor.execute(insert_site_announcement(), (ann_title, ann_msg, ann_enabled))
            log_audit_event("ADMIN_ANNOUNCEMENT_UPDATE", f"Updated site announcement (enabled={ann_enabled})", user_id=admin_id)
            flash("Platform announcement and enterprise settings updated successfully.", "success")
        except Exception as e:
            print("Save Site Announcement Error:", e)
            flash(f"Failed to save announcement: {str(e)}", "error")
        return redirect(url_for("admin.settings"))

    from auth.routes import _get_payment_settings_dict
    return render_template("admin/settings.html", announcement=fetch_site_announcement(), payment_settings=_get_payment_settings_dict())


@admin_bp.route("/settings/payment", methods=["POST"])
@admin_required
def update_payment_settings_route():
    """
    Lets the Super Admin edit the bank/JazzCash/Easypaisa account details shown to
    users on the registration payment page and the renewal screen - these were
    previously hardcoded in the templates, so changing an account meant editing code.
    """
    admin_id = session.get("admin_user_id") or session.get("user_id")
    plan_name = request.form.get("plan_name", "").strip() or "Enterprise Plan"
    plan_price_label = request.form.get("plan_price_label", "").strip() or "PKR 25,000 / month"
    account_title = request.form.get("account_title", "").strip()
    bank_name = request.form.get("bank_name", "").strip()
    account_number = request.form.get("account_number", "").strip()
    iban = request.form.get("iban", "").strip()
    jazzcash_number = request.form.get("jazzcash_number", "").strip()
    easypaisa_number = request.form.get("easypaisa_number", "").strip()
    sadapay_number = request.form.get("sadapay_number", "").strip()
    instructions = request.form.get("instructions", "").strip()

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                update_payment_settings(),
                (plan_name, plan_price_label, account_title, bank_name, account_number,
                 iban, jazzcash_number, easypaisa_number, sadapay_number, instructions)
            )
        log_audit_event("ADMIN_PAYMENT_SETTINGS_UPDATE", "Updated payment account details shown to users", user_id=admin_id)
        flash("Payment account details updated successfully.", "success")
    except Exception as e:
        print("Update Payment Settings Error:", e)
        flash(f"Failed to update payment settings: {str(e)}", "error")

    return redirect(url_for("admin.settings"))


@admin_bp.route("/settings/backup-now", methods=["POST"])
@admin_required
def trigger_backup_now():
    """
    On-demand database backup, in addition to the automatic daily one the scheduler
    already runs (utils/db_backup.py) - useful right before a risky manual DB change.
    """
    from utils.db_backup import run_database_backup
    admin_id = session.get("admin_user_id") or session.get("user_id")
    backup_path = run_database_backup()
    if backup_path:
        log_audit_event("ADMIN_MANUAL_BACKUP", f"Manual database backup created: {backup_path}", user_id=admin_id)
        flash(f"✅ Database backup created successfully: {backup_path}", "success")
    else:
        flash("❌ Database backup failed. Check server logs for details.", "error")
    return redirect(url_for("admin.settings"))


@admin_bp.route("/subscribers")
@admin_required
def subscribers():
    """
    Super Admin Newsletter Subscribers Monitor with live search filter & status counts.
    """
    subscribers_list = []
    search_query = request.args.get("q", "").strip().lower()

    try:
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT SubscriberID, Email, Status, IPAddress, SubscribedAt 
                FROM NewsletterSubscribers 
                ORDER BY SubscribedAt DESC
            """)
            raw_sub = cursor.fetchall() or []

        for row in raw_sub:
            sub_id, email, status, ip_addr, sub_at = row
            if search_query and (search_query not in email.lower() and search_query not in (status or "").lower()):
                continue

            subscribers_list.append({
                "id": sub_id,
                "email": email,
                "status": status or "Active",
                "ip_address": ip_addr or "127.0.0.1",
                "subscribed_at": format_12hr_datetime(sub_at)
            })
    except Exception as e:
        print("Admin Subscribers Fetch Error:", e)

    active_count = sum(1 for s in subscribers_list if s["status"].lower() == "active")
    return render_template("admin/subscribers.html", subscribers=subscribers_list, active_count=active_count, search_query=search_query)


@admin_bp.route("/subscribers/delete/<int:subscriber_id>", methods=["POST"])
@admin_required
def delete_subscriber(subscriber_id):
    """
    Super Admin Delete Newsletter Subscriber.
    """
    admin_id = session.get("admin_user_id") or session.get("user_id")
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("SELECT Email FROM NewsletterSubscribers WHERE SubscriberID = ?", (subscriber_id,))
            s_row = cursor.fetchone()
            if s_row:
                s_email = s_row[0]
                cursor.execute("DELETE FROM NewsletterSubscribers WHERE SubscriberID = ?", (subscriber_id,))
                log_audit_event("ADMIN_SUBSCRIBER_DELETED", f"Deleted newsletter subscriber #{subscriber_id} ({s_email})", user_id=admin_id)
                flash(f"Subscriber '{s_email}' deleted successfully.", "success")
            else:
                flash("Subscriber record not found.", "error")
    except Exception as e:
        print("Delete Subscriber Error:", e)
        flash(f"Failed to delete subscriber: {str(e)}", "error")

    return redirect(url_for("admin.subscribers"))


@admin_bp.route("/contact-messages")
@admin_required
def contact_messages():
    """
    Super Admin console for Contact Us form submissions from the public website,
    so customer queries/complaints are visible and actionable instead of disappearing.
    """
    messages_list = []
    search_query = request.args.get("q", "").strip().lower()
    status_filter = request.args.get("status", "").strip()

    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_all_contact_messages())
            raw_rows = cursor.fetchall() or []

        for row in raw_rows:
            msg_id, full_name, email, subject, message, status, ip_addr, created_at = row[:8]
            admin_reply = row[8] if len(row) > 8 else None
            replied_at = row[9] if len(row) > 9 else None
            status = status or "New"

            if status_filter and status.lower() != status_filter.lower():
                continue
            if search_query and not any(
                search_query in (val or "").lower() for val in [full_name, email, subject, message]
            ):
                continue

            messages_list.append({
                "id": msg_id,
                "full_name": full_name,
                "email": email,
                "subject": subject,
                "message": message,
                "status": status,
                "ip_address": ip_addr or "-",
                "created_at": format_12hr_datetime(created_at),
                "admin_reply": admin_reply,
                "replied_at": format_12hr_datetime(replied_at) if replied_at else None
            })
    except Exception as e:
        print("Admin Contact Messages Fetch Error:", e)

    new_count = sum(1 for m in messages_list if m["status"].lower() == "new")
    return render_template(
        "admin/contact_messages.html",
        messages=messages_list,
        new_count=new_count,
        search_query=search_query,
        status_filter=status_filter
    )


@admin_bp.route("/contact-messages/status/<int:message_id>", methods=["POST"])
@admin_required
def update_contact_message(message_id):
    """
    Mark a contact message as Read / Responded / New so the team can track follow-up.
    """
    admin_id = session.get("admin_user_id") or session.get("user_id")
    new_status = request.form.get("status", "").strip()

    if new_status not in ["New", "Read", "Responded"]:
        flash("Invalid status selection.", "error")
        return redirect(url_for("admin.contact_messages"))

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(update_contact_message_status(), (new_status, message_id))
        log_audit_event("ADMIN_CONTACT_MESSAGE_STATUS", f"Marked contact message #{message_id} as '{new_status}'", user_id=admin_id)
        flash(f"Message #{message_id} marked as '{new_status}'.", "success")
    except Exception as e:
        print("Update Contact Message Status Error:", e)
        flash(f"Failed to update message status: {str(e)}", "error")

    return redirect(url_for("admin.contact_messages"))


@admin_bp.route("/contact-messages/reply/<int:message_id>", methods=["POST"])
@admin_required
def reply_contact_message(message_id):
    """
    Super Admin replies to a customer's Contact Us submission directly from the console;
    the reply is emailed straight to the customer's registered address (quoting their
    original message), saved for a record, and the message is auto-marked 'Responded'.
    """
    admin_id = session.get("admin_user_id") or session.get("user_id")
    reply_text = (request.form.get("reply_message") or "").strip()

    if not reply_text:
        flash("Reply message cannot be empty.", "error")
        return redirect(url_for("admin.contact_messages"))

    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_contact_message_by_id(), (message_id,))
            row = cursor.fetchone()

        if not row:
            flash("Message not found.", "error")
            return redirect(url_for("admin.contact_messages"))

        _, full_name, email, subject, original_message = row

        with get_db_cursor(commit=True) as cursor:
            cursor.execute(save_contact_message_reply(), (reply_text, message_id))

        if email:
            from auth.email_service import EmailService
            email_service = EmailService()
            email_service.send_contact_reply_email(
                to_email=email,
                user_name=full_name or "Valued Customer",
                original_subject=subject or "Your Inquiry",
                original_message=original_message or "",
                admin_reply=reply_text
            )

        log_audit_event("ADMIN_CONTACT_MESSAGE_REPLIED", f"Replied to contact message #{message_id} from {email}", user_id=admin_id)
        flash(f"Reply sent successfully to {escape(email or 'the customer')}!", "success")
    except Exception as e:
        print("Reply Contact Message Error:", e)
        flash(f"Failed to send reply: {str(e)}", "error")

    return redirect(url_for("admin.contact_messages"))


@admin_bp.route("/contact-messages/delete/<int:message_id>", methods=["POST"])
@admin_required
def delete_contact_message_route(message_id):
    """
    Super Admin Delete Contact Message.
    """
    admin_id = session.get("admin_user_id") or session.get("user_id")
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(delete_contact_message(), (message_id,))
        log_audit_event("ADMIN_CONTACT_MESSAGE_DELETED", f"Deleted contact message #{message_id}", user_id=admin_id)
        flash(f"Message #{message_id} deleted successfully.", "success")
    except Exception as e:
        print("Delete Contact Message Error:", e)
        flash(f"Failed to delete message: {str(e)}", "error")

    return redirect(url_for("admin.contact_messages"))
