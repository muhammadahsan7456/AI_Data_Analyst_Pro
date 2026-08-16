import os
import uuid
import secrets
import threading
from datetime import datetime
from PIL import Image
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    g,
    jsonify,
    current_app
)

from auth import auth_bp
from auth.security import (
    hash_password,
    verify_password,
    generate_secure_token,
    validate_password_strength,
    log_login_attempt,
    log_audit_event,
    get_client_ip
)
from auth.decorators import login_required, admin_required, manager_required, role_required

from auth.email_service import EmailService

from database.connection import get_db_cursor
from database.queries import (
    get_user_by_email,
    get_user_by_username,
    get_user_by_id,
    create_user,
    update_user_password,
    update_user_profile_info,
    update_user_avatar,
    verify_user_email,
    increment_failed_login,
    reset_failed_login,
    get_user_profile,
    upsert_user_profile,
    get_user_settings,
    upsert_user_settings,
    create_email_verification_token,
    get_email_verification_token,
    increment_email_token_attempt,
    mark_email_token_used,
    create_password_reset_token,
    get_password_reset_token,
    increment_reset_token_attempt,
    mark_reset_token_used,
    check_resend_rate_limit,
    get_user_login_history,
    create_user_session,
    invalidate_user_session,
    invalidate_all_other_sessions,
    get_active_sessions_for_user,
    get_user_audit_logs
)

from ai.smart_assistant import (
    trigger_ai_event,
    build_welcome_back_card,
    build_farewell_card,
    build_signup_card,
    build_email_verified_card,
    build_forgot_password_card,
    build_password_changed_card,
    build_profile_updated_card,
    build_avatar_updated_card,
    build_security_alert_card,
    build_failed_login_card,
    build_account_locked_card,
    parse_client_device
)

email_service = EmailService()


# ==========================================
# API: LIVE USERNAME / EMAIL AVAILABILITY
# ==========================================
@auth_bp.route("/check-availability", methods=["POST"])
def check_availability():
    data = request.get_json() or {}
    field = data.get("field")
    value = data.get("value", "").strip()

    if not field or not value:
        return jsonify({"available": False, "message": "Invalid query"})

    with get_db_cursor() as cursor:
        if field == "username":
            cursor.execute(get_user_by_username(), (value,))
            exists = cursor.fetchone() is not None
            return jsonify({"available": not exists, "message": "Username is available!" if not exists else "Username is already taken."})
        elif field == "email":
            cursor.execute(get_user_by_email(), (value,))
            exists = cursor.fetchone() is not None
            return jsonify({"available": not exists, "message": "Email is available!" if not exists else "Email is already registered."})

    return jsonify({"available": False, "message": "Unknown field"})


# ==========================================
# SIGNUP / REGISTER ROUTE
# ==========================================
@auth_bp.route("/signup", methods=["GET", "POST"])
@auth_bp.route("/register", methods=["GET", "POST"])
def signup():
    if session.get("user_id"):
        return redirect(url_for("frontend.dashboard"))

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        country = request.form.get("country", "").strip()
        city = request.form.get("city", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        terms = request.form.get("terms") == "on"

        # Preserve form data in session for Change Email pre-fill capability
        session["signup_form_data"] = {
            "first_name": first_name,
            "last_name": last_name,
            "username": username,
            "email": email,
            "phone": phone,
            "country": country,
            "city": city
        }

        if not terms:
            flash("You must agree to the Terms of Service and Privacy Policy.", "error")
            return render_template("signup.html", form_data=request.form)

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("signup.html", form_data=request.form)

        is_strong, msg = validate_password_strength(password)
        if not is_strong:
            flash(msg, "error")
            return render_template("signup.html", form_data=request.form)

        pending_session = session.get("pending_otp") or {}
        pending_user_id = pending_session.get("user_id")

        with get_db_cursor() as cursor:
            # Email uniqueness check (ignoring current unverified pending user if updating email)
            cursor.execute(get_user_by_email(), (email,))
            existing_email_user = cursor.fetchone()
            if existing_email_user and existing_email_user[0] != pending_user_id:
                flash("An account with this email address already exists. Please sign in instead.", "error")
                return render_template("signup.html", form_data=request.form)

            # Username uniqueness check (ignoring current unverified pending user)
            cursor.execute(get_user_by_username(), (username,))
            existing_uname_user = cursor.fetchone()
            if existing_uname_user and existing_uname_user[0] != pending_user_id:
                flash(f"Username '{username}' is already taken. Please choose a different username.", "error")
                return render_template("signup.html", form_data=request.form)

        # Hash password using bcrypt
        password_hash = hash_password(password)
        full_name = f"{first_name} {last_name}".strip() or username

        try:
            new_user_id = None
            is_updating_existing = False

            if pending_user_id:
                # Check if pending_user_id exists and is unverified
                with get_db_cursor() as cursor:
                    cursor.execute("SELECT UserID, IsVerified FROM Users WHERE UserID = ?", (pending_user_id,))
                    pending_row = cursor.fetchone()
                    if pending_row and not pending_row[1]:
                        is_updating_existing = True

            if is_updating_existing:
                new_user_id = pending_user_id
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute(
                        "UPDATE Users SET FirstName = ?, LastName = ?, Username = ?, FullName = ?, Email = ?, PhoneNumber = ?, Country = ?, City = ?, PasswordHash = ? WHERE UserID = ? AND IsVerified = 0",
                        (first_name, last_name, username, full_name, email, phone, country, city, password_hash, new_user_id)
                    )
            else:
                with get_db_cursor(commit=True) as cursor:
                    try:
                        cursor.execute(
                            create_user(),
                            (first_name, last_name, username, full_name, email, phone, country, city, password_hash)
                        )
                        res = cursor.fetchone()
                        if res:
                            new_user_id = int(res[0])
                    except Exception as insert_err:
                        print("Insert User fetchone notice:", insert_err)

                if not new_user_id:
                    with get_db_cursor() as cursor:
                        cursor.execute(get_user_by_email(), (email,))
                        u = cursor.fetchone()
                        if u:
                            new_user_id = u[0]

            if not new_user_id:
                raise ValueError("Failed to retrieve User ID for registration.")

            # Generate 6-Digit Verification OTP Code using secrets
            otp_code = f"{secrets.randbelow(900000) + 100000}"

            with get_db_cursor(commit=True) as cursor:
                cursor.execute(create_email_verification_token(), (new_user_id, otp_code))

            # Store pending OTP & user details in session
            session["pending_otp"] = {
                "user_id": new_user_id,
                "otp_code": otp_code,
                "email": email,
                "phone": phone or "",
                "first_name": first_name,
                "last_name": last_name,
                "username": username,
                "country": country,
                "city": city,
                "channel": "email",
                "full_name": full_name,
                "created_at": datetime.now().isoformat(),
                "attempts": 0
            }

            # Dispatch 6-digit OTP via Email asynchronously in background thread for instant sub-second response
            threading.Thread(target=email_service.send_verification_email, args=(email, full_name, otp_code), daemon=True).start()
            log_audit_event("SIGNUP_OTP_EMAIL", f"6-Digit Email OTP generated for {email}: {otp_code}", user_id=new_user_id)

            flash(f"🔐 Account details updated! A 6-digit verification code has been sent to your email address (<strong>{email}</strong>). Please check your Gmail inbox.", "info")

            return redirect(url_for("auth.verify_otp"))

        except Exception as e:
            flash(f"Signup Error: {str(e)}", "error")
            return render_template("signup.html", form_data=request.form)

    # GET Request handling: Pre-fill all fields if user clicked Change Email or has pending OTP session
    form_data = None
    pending = session.get("pending_otp") or {}
    saved_form = session.get("signup_form_data") or {}

    if request.args.get("change_email") == "1" or request.args.get("edit") == "1" or pending or saved_form:
        form_data = {
            "first_name": saved_form.get("first_name") or pending.get("first_name", ""),
            "last_name": saved_form.get("last_name") or pending.get("last_name", ""),
            "username": saved_form.get("username") or pending.get("username", ""),
            "email": saved_form.get("email") or pending.get("email", ""),
            "phone": saved_form.get("phone") or pending.get("phone", ""),
            "country": saved_form.get("country") or pending.get("country", ""),
            "city": saved_form.get("city") or pending.get("city", "")
        }

    return render_template("signup.html", form_data=form_data)


# ==========================================
# 6-DIGIT OTP VERIFICATION ROUTE
# ==========================================
@auth_bp.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    pending = session.get("pending_otp")
    if not pending:
        flash("No pending OTP verification session found. Please sign in or register.", "warning")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        # Handle single input or 6 individual digit boxes
        input_code = request.form.get("otp_code", "").strip()
        if not input_code:
            digits = [request.form.get(f"digit{i}", "").strip() for i in range(1, 7)]
            input_code = "".join(digits)

        expected_code = str(pending.get("otp_code", "")).strip()
        user_id = pending.get("user_id")
        full_name = pending.get("full_name", "User")

        # Check DB attempts and expiration
        db_token = None
        with get_db_cursor() as cursor:
            cursor.execute(get_email_verification_token(), (expected_code,))
            db_token = cursor.fetchone()

        attempts = pending.get("attempts", 0)
        if db_token:
            attempts = db_token[5]

        if attempts >= 5:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(mark_email_token_used(), (expected_code,))
            session.pop("pending_otp", None)
            flash("❌ Maximum verification attempts exceeded (5 failed attempts). Verification code invalidated. Please request a new code.", "error")
            return redirect(url_for("auth.login"))

        if input_code == expected_code:
            try:
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute(verify_user_email(), (user_id,))
                    cursor.execute(mark_email_token_used(), (expected_code,))

                log_audit_event("VERIFY_OTP_SUCCESS", "Account verified successfully via 6-digit OTP.", user_id=user_id)
                trigger_ai_event(user_id, build_email_verified_card(full_name))
                session.pop("pending_otp", None)

                session["toast"] = {
                    "type": "success",
                    "title": f"👋 Hello {full_name}!",
                    "message": "Your email address has been verified successfully. You can now log in."
                }
                flash(f"👋 Hello {full_name}! Your email has been verified successfully.", "success")
                return redirect(url_for("auth.login"))
            except Exception as err:
                flash(f"Verification error: {str(err)}", "error")
        else:
            attempts += 1
            pending["attempts"] = attempts
            session["pending_otp"] = pending

            try:
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute(increment_email_token_attempt(), (expected_code,))
            except Exception: pass

            remaining = 5 - attempts
            if remaining <= 0:
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute(mark_email_token_used(), (expected_code,))
                session.pop("pending_otp", None)
                flash("❌ Maximum verification attempts exceeded. Please request a new verification code.", "error")
                return redirect(url_for("auth.login"))
            else:
                flash(f"❌ Invalid verification code. {remaining} attempt(s) remaining.", "error")

    return render_template("verify_otp.html", pending_otp=pending)


# ==========================================
# RESEND OTP ROUTE
# ==========================================
@auth_bp.route("/resend-otp", methods=["POST"])
def resend_otp():
    pending = session.get("pending_otp")
    if not pending:
        flash("No pending OTP session found. Please register again.", "warning")
        return redirect(url_for("auth.login"))

    user_id = pending.get("user_id")
    email = pending.get("email")
    phone = pending.get("phone")
    channel = pending.get("channel", "email")
    full_name = pending.get("full_name", "User")

    # Rate Limit Check: Max 3 resend requests within 15 minutes
    try:
        with get_db_cursor() as cursor:
            cursor.execute(check_resend_rate_limit(), (user_id,))
            cnt = cursor.fetchone()
            if cnt and cnt[0] >= 3:
                flash("Too many requests. Please try again later.", "error")
                return redirect(url_for("auth.verify_otp"))
    except Exception: pass

    # Invalidate previous OTP
    old_code = pending.get("otp_code")
    if old_code:
        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(mark_email_token_used(), (old_code,))
        except Exception: pass

    new_otp = f"{secrets.randbelow(900000) + 100000}"
    pending["otp_code"] = new_otp
    pending["attempts"] = 0
    session["pending_otp"] = pending

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(create_email_verification_token(), (user_id, new_otp))
    except Exception: pass

    threading.Thread(target=email_service.send_verification_email, args=(email, full_name, new_otp), daemon=True).start()
    log_audit_event("RESEND_OTP_EMAIL", f"New Email OTP sent to {email}: {new_otp}", user_id=user_id)
    flash(f"📧 A new 6-digit verification code has been sent to your email address (<strong>{email}</strong>). Please check your Gmail inbox.", "info")

    return redirect(url_for("auth.verify_otp"))


# ==========================================
# REAL-TIME USER AVAILABILITY CHECK API
# ==========================================
@auth_bp.route("/api/check-user")
def check_user_api():
    email = request.args.get("email", "").strip().lower()
    username = request.args.get("username", "").strip()

    with get_db_cursor() as cursor:
        if email:
            cursor.execute(get_user_by_email(), (email,))
            if cursor.fetchone():
                return jsonify({"available": False, "message": "An account with this email address already exists."})

        if username:
            cursor.execute(get_user_by_username(), (username,))
            if cursor.fetchone():
                return jsonify({"available": False, "message": f"Username '{username}' is already taken."})

    return jsonify({"available": True, "message": "Available"})


# ==========================================
# VERIFY EMAIL ROUTE
# ==========================================
@auth_bp.route("/verify-email/<token>")
def verify_email(token):
    with get_db_cursor() as cursor:
        cursor.execute(get_email_verification_token(), (token,))
        tok_data = cursor.fetchone()

    if not tok_data:
        flash("Invalid or expired email verification token.", "error")
        return redirect(url_for("auth.login"))

    user_id = tok_data[1]
    try:
        user_name = "User"
        with get_db_cursor() as cursor:
            cursor.execute(get_user_by_id(), (user_id,))
            u = cursor.fetchone()
            if u:
                user_name = u[4] or u[3]

        with get_db_cursor(commit=True) as cursor:
            cursor.execute(verify_user_email(), (user_id,))
            cursor.execute(mark_email_token_used(), (token,))

        log_audit_event("VERIFY_EMAIL", "Email verified successfully.", user_id=user_id)
        trigger_ai_event(user_id, build_email_verified_card(user_name))
        flash("🎉 Your email address has been verified successfully! You can now log in.", "success")
    except Exception as e:
        flash(f"Verification error: {str(e)}", "error")

    return redirect(url_for("auth.login"))


# ==========================================
# RESEND VERIFICATION EMAIL ROUTE
# ==========================================
@auth_bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    email = request.form.get("email", "").strip().lower()
    with get_db_cursor() as cursor:
        cursor.execute(get_user_by_email(), (email,))
        user = cursor.fetchone()

    if user and not user[12]:  # user[12] is IsVerified
        user_id = user[0]
        full_name = user[4] or user[3]
        token = generate_secure_token()

        with get_db_cursor(commit=True) as cursor:
            cursor.execute(create_email_verification_token(), (user_id, token))

        verification_link = url_for("auth.verify_email", token=token, _external=True)
        email_service.send_verification_email(email, full_name, verification_link)

        if email_service._is_configured():
            flash(f"Verification link resent to <strong>{email}</strong>!", "info")
        else:
            flash(f"Verification link generated for <strong>{email}</strong>! <a href='{verification_link}' class='btn btn-success btn-sm' style='margin-left: 12px; font-weight: bold;'>✉️ Click Here to Verify Email Now</a>", "warning")

    return redirect(url_for("auth.login"))


# ==========================================
# LOGIN ROUTE
# ==========================================
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("frontend.dashboard"))

    if request.method == "POST":
        email_or_username = request.form.get("email_or_username", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"

        with get_db_cursor() as cursor:
            cursor.execute(get_user_by_email(), (email_or_username,))
            user = cursor.fetchone()
            if not user:
                cursor.execute(get_user_by_username(), (email_or_username,))
                user = cursor.fetchone()

        if not user:
            trigger_ai_event(None, build_failed_login_card(), save_to_history=False)
            flash("Invalid email/username or password.", "error")
            return render_template("login.html")

        user_id, fn, ln, un, fnm, em, ph, cnt, ct, pwd_hash, pimg, is_active, is_verified, role, failed_attempts, lockout_until, created_at = user

        # Check Account Lockout
        if lockout_until:
            try:
                lockout_dt = datetime.fromisoformat(str(lockout_until)) if isinstance(lockout_until, str) else lockout_until
                if datetime.now() < lockout_dt:
                    trigger_ai_event(user_id, build_account_locked_card())
                    flash("Account is temporarily locked due to multiple failed login attempts. Please try again later or reset your password.", "error")
                    log_login_attempt(user_id, "Locked")
                    return render_template("login.html")
            except Exception: pass

        # Verify Password
        if not verify_password(password, pwd_hash):
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(increment_failed_login(), (user_id,))

            log_login_attempt(user_id, "Failed")
            trigger_ai_event(user_id, build_failed_login_card())
            flash("Invalid email/username or password.", "error")
            return render_template("login.html")

        # Check Email Verification
        if not is_verified:
            otp_code = f"{secrets.randbelow(900000) + 100000}"
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(create_email_verification_token(), (user_id, otp_code))

            email_service.send_verification_email(em, fnm or un, otp_code)
            log_login_attempt(user_id, "Unverified")

            session["pending_otp"] = {
                "user_id": user_id,
                "otp_code": otp_code,
                "email": em,
                "full_name": fnm or un,
                "created_at": datetime.now().isoformat(),
                "attempts": 0
            }
            flash("Please verify your email before logging in.", "warning")
            return render_template("login.html", unverified_email=em)

        # Reset failed attempts
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(reset_failed_login(), (user_id,))

        user_display_name = fnm or un or "User"

        # Establish Flask Session
        session.permanent = remember
        session["user_id"] = user_id
        session["user_name"] = user_display_name
        session["user_email"] = em
        session["user_role"] = role or "Analyst"

        # Create Active Session Token
        session_token = generate_secure_token()
        session["session_token"] = session_token
        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(create_user_session(), (user_id, session_token))
        except Exception: pass

        log_login_attempt(user_id, "Success")
        log_audit_event("LOGIN", "User logged in successfully.", user_id=user_id)

        # Set Login Toast
        session["toast"] = {
            "type": "success",
            "title": f"👋 Hello {user_display_name}!",
            "message": "You have successfully logged in."
        }

        # Detect New Device / Security Check
        client_info = parse_client_device(request.headers.get("User-Agent", ""))
        is_new_device = False
        try:
            with get_db_cursor() as cursor:
                cursor.execute("""
                    SELECT COUNT(*)
                    FROM LoginHistory
                    WHERE UserID = ? AND Status = 'Success' AND UserAgent = ?
                """, (user_id, request.headers.get("User-Agent", "")))
                prev_cnt = cursor.fetchone()
                if prev_cnt and prev_cnt[0] <= 1:
                    is_new_device = True
        except Exception: pass

        if is_new_device:
            trigger_ai_event(
                user_id,
                build_security_alert_card(
                    user_display_name,
                    client_info["device"],
                    client_info["browser"],
                    client_info["location"]
                )
            )
            try:
                email_service.send_login_notification_email(em, user_display_name, datetime.now().strftime("%Y-%m-%d %H:%M UTC"), client_info["device"])
            except Exception: pass
        else:
            trigger_ai_event(
                user_id,
                build_welcome_back_card(user_id, user_display_name)
            )

        next_page = request.args.get("next")
        flash(f"👋 Hello {user_display_name}! You have successfully logged in.", "success")
        return redirect(next_page or url_for("frontend.dashboard"))

    return render_template("login.html")


# ==========================================
# FORGOT PASSWORD ROUTE
# ==========================================
@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        user = None
        if email:
            with get_db_cursor() as cursor:
                cursor.execute(get_user_by_email(), (email,))
                user = cursor.fetchone()

        if user:
            user_id = user[0]
            full_name = user[4] or user[3]
            otp_code = f"{secrets.randbelow(900000) + 100000}"

            with get_db_cursor(commit=True) as cursor:
                cursor.execute(create_password_reset_token(), (user_id, otp_code))

            reset_link = url_for("auth.reset_password", token=otp_code, _external=True)
            email_service.send_password_reset_email(email, full_name, otp_code, reset_link)

            log_audit_event("FORGOT_PASSWORD_REQUEST", f"Password reset OTP generated for {email}", user_id=user_id)
            trigger_ai_event(user_id, build_forgot_password_card(full_name))

        # Always flash generic message to prevent account enumeration
        flash("If an account exists with this email, a password reset code has been sent.", "info")
        return redirect(url_for("auth.reset_password", token=email if user else "verify"))

    return render_template("forgot_password.html")


# ==========================================
# RESET PASSWORD ROUTE
# ==========================================
@auth_bp.route("/reset-password", methods=["GET", "POST"])
@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token=""):
    if request.method == "POST":
        input_token = request.form.get("token", token).strip()
        otp_code = request.form.get("otp_code", "").strip() or input_token
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", token=token, otp_code=otp_code)

        is_strong, msg = validate_password_strength(password)
        if not is_strong:
            flash(msg, "error")
            return render_template("reset_password.html", token=token, otp_code=otp_code)

        with get_db_cursor() as cursor:
            cursor.execute(get_password_reset_token(), (otp_code,))
            tok_data = cursor.fetchone()

        if not tok_data:
            # Check if token exists but failed attempts exceeded or expired
            try:
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute(increment_reset_token_attempt(), (otp_code,))
            except Exception: pass
            flash("Invalid or expired password reset code.", "error")
            return render_template("reset_password.html", token=token, otp_code=otp_code)

        user_id = tok_data[1]
        attempts = tok_data[5] if len(tok_data) > 5 else 0

        if attempts >= 5:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(mark_reset_token_used(), (otp_code,))
            flash("❌ Maximum verification attempts exceeded. Password reset code invalidated.", "error")
            return redirect(url_for("auth.forgot_password"))

        # Fetch user name for toast
        user_name = "User"
        with get_db_cursor() as cursor:
            cursor.execute(get_user_by_id(), (user_id,))
            u = cursor.fetchone()
            if u:
                user_name = u[4] or u[3]

        new_hash = hash_password(password)
        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(update_user_password(), (new_hash, user_id))
                cursor.execute(mark_reset_token_used(), (otp_code,))

            log_audit_event("PASSWORD_RESET_SUCCESS", "Password reset successfully via 6-digit OTP.", user_id=user_id)
            trigger_ai_event(user_id, build_password_changed_card())

            session["toast"] = {
                "type": "success",
                "title": f"Hello {user_name}!",
                "message": "Your password has been reset successfully. Please log in with your new password."
            }
            flash("Your password has been reset successfully! Please log in with your new password.", "success")
            return redirect(url_for("auth.login"))
        except Exception as e:
            flash(f"Error resetting password: {str(e)}", "error")

    return render_template("reset_password.html", token=token)


# ==========================================
# CHANGE PASSWORD ROUTE
# ==========================================
@auth_bp.route("/change-password", methods=["POST"])
@login_required
def change_password():
    user_id = session.get("user_id")
    current_pwd = request.form.get("current_password", "")
    new_pwd = request.form.get("new_password", "")
    confirm_pwd = request.form.get("confirm_password", "")

    if new_pwd != confirm_pwd:
        flash("New passwords do not match.", "error")
        return redirect(url_for("auth.profile"))

    with get_db_cursor() as cursor:
        cursor.execute(get_user_by_id(), (user_id,))
        user = cursor.fetchone()

    if not user or not verify_password(current_pwd, user[9]):
        flash("Incorrect current password.", "error")
        return redirect(url_for("auth.profile"))

    is_strong, msg = validate_password_strength(new_pwd)
    if not is_strong:
        flash(msg, "error")
        return redirect(url_for("auth.profile"))

    new_hash = hash_password(new_pwd)
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(update_user_password(), (new_hash, user_id))

        log_audit_event("PASSWORD_CHANGE", "Password changed from profile settings.", user_id=user_id)
        trigger_ai_event(user_id, build_password_changed_card())
        flash("Password updated successfully!", "success")
    except Exception as e:
        flash(f"Failed to update password: {str(e)}", "error")

    return redirect(url_for("auth.profile"))


# ==========================================
# USER PROFILE & AVATAR UPLOAD ROUTE
# ==========================================
@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user_id = session.get("user_id")

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        country = request.form.get("country", "").strip()
        city = request.form.get("city", "").strip()

        bio = request.form.get("bio", "").strip()
        occupation = request.form.get("occupation", "").strip()
        company = request.form.get("company", "").strip()
        department = request.form.get("department", "").strip()
        designation = request.form.get("designation", "").strip()
        website = request.form.get("website", "").strip()
        linkedin = request.form.get("linkedin", "").strip()
        github = request.form.get("github", "").strip()
        portfolio = request.form.get("portfolio", "").strip()
        timezone = request.form.get("timezone", "UTC").strip()
        language = request.form.get("language", "en").strip()

        full_name = f"{first_name} {last_name}".strip() or username

        # Handle Profile Photo Upload
        avatar_file = request.files.get("avatar")
        avatar_path = None
        if avatar_file and avatar_file.filename:
            ext = os.path.splitext(avatar_file.filename)[1].lower()
            if ext in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"]:
                filename = f"user_{user_id}_{uuid.uuid4().hex[:8]}{ext}"
                save_dir = os.path.join("static", "images", "profiles")
                os.makedirs(save_dir, exist_ok=True)
                full_save_path = os.path.join(save_dir, filename)

                # Resize image using Pillow if non-SVG
                try:
                    if ext == ".svg":
                        avatar_file.save(full_save_path)
                    else:
                        img = Image.open(avatar_file)
                        img.thumbnail((300, 300))
                        img.save(full_save_path)
                    avatar_path = f"images/profiles/{filename}"
                except Exception as img_err:
                    print("Image resize warning:", img_err)
                    avatar_path = None
            else:
                flash("Invalid profile avatar file format! Only image files (.jpg, .jpeg, .png, .webp, .gif, .svg) are allowed.", "error")

        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(
                    update_user_profile_info(),
                    (first_name, last_name, full_name, username, email, phone, country, city, user_id)
                )

                if avatar_path:
                    cursor.execute(update_user_avatar(), (avatar_path, user_id))

                cursor.execute(
                    upsert_user_profile(),
                    (
                        user_id, bio, occupation, company, department, designation, website, linkedin, github, portfolio, timezone, language, user_id,
                        user_id, bio, occupation, company, department, designation, website, linkedin, github, portfolio, timezone, language
                    )
                )

            session["user_name"] = full_name
            log_audit_event("PROFILE_UPDATE", "User profile updated.", user_id=user_id)

            if avatar_path:
                trigger_ai_event(user_id, build_avatar_updated_card(full_name))
            else:
                trigger_ai_event(user_id, build_profile_updated_card(full_name))

            flash("Profile updated successfully!", "success")
        except Exception as e:
            flash(f"Profile update error: {str(e)}", "error")

        return redirect(url_for("auth.profile"))

    # Fetch User & Profile data
    with get_db_cursor() as cursor:
        cursor.execute(get_user_by_id(), (user_id,))
        user = cursor.fetchone()

        cursor.execute(get_user_profile(), (user_id,))
        prof = cursor.fetchone()

    return render_template("profile.html", user=user, profile=prof)


# ==========================================
# ACCOUNT SETTINGS ROUTE
# ==========================================
@auth_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    user_id = session.get("user_id")

    if request.method == "POST":
        theme = request.form.get("theme", "light")
        date_format = request.form.get("date_format", "YYYY-MM-DD")
        default_export = request.form.get("default_export", "csv")
        chart_pref = request.form.get("chart_pref", "bar")
        dashboard_pref = request.form.get("dashboard_pref", "standard")

        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(
                    upsert_user_settings(),
                    (
                        user_id, theme, date_format, default_export, chart_pref, dashboard_pref, user_id,
                        user_id, theme, date_format, default_export, chart_pref, dashboard_pref
                    )
                )

            log_audit_event("SETTINGS_UPDATE", f"User updated settings to theme={theme}", user_id=user_id)
            flash("Settings saved successfully!", "success")
        except Exception as e:
            flash(f"Settings error: {str(e)}", "error")

        return redirect(url_for("auth.settings"))

    with get_db_cursor() as cursor:
        cursor.execute(get_user_settings(), (user_id,))
        user_sett = cursor.fetchone()

    return render_template("settings.html", settings=user_sett)


# ==========================================
# SECURITY & ACTIVE SESSIONS ROUTE
# ==========================================
@auth_bp.route("/security", methods=["GET", "POST"])
@login_required
def security():
    user_id = session.get("user_id")

    if request.method == "POST":
        action = request.form.get("action")
        if action == "logout_other_devices":
            current_token = session.get("session_token", "")
            try:
                with get_db_cursor(commit=True) as cursor:
                    cursor.execute(invalidate_all_other_sessions(), (user_id, current_token))

                log_audit_event("LOGOUT_OTHER_DEVICES", "Logged out all other active devices.", user_id=user_id)
                flash("Logged out of all other devices successfully!", "success")
            except Exception as e:
                flash(f"Error logging out devices: {str(e)}", "error")

            return redirect(url_for("auth.security"))

    with get_db_cursor() as cursor:
        cursor.execute(get_user_login_history(limit=10), (user_id,))
        login_history = cursor.fetchall()

        cursor.execute(get_active_sessions_for_user(), (user_id,))
        active_sessions = cursor.fetchall()

        cursor.execute(get_user_audit_logs(limit=15), (user_id,))
        audit_logs = cursor.fetchall()

    return render_template(
        "security.html",
        login_history=login_history,
        active_sessions=active_sessions,
        audit_logs=audit_logs
    )


# ==========================================
# LOGOUT ROUTE
# ==========================================
@auth_bp.route("/logout")
def logout():
    user_id = session.get("user_id")
    user_name = session.get("user_name", "User")
    session_token = session.get("session_token")

    if session_token:
        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(invalidate_user_session(), (session_token,))
        except Exception: pass

    log_audit_event("LOGOUT", "User logged out.", user_id=user_id)

    # Build farewell card BEFORE clearing session, then attach to new session
    farewell_card = build_farewell_card(user_name)
    if user_id:
        trigger_ai_event(user_id, farewell_card)

    session.clear()
    session["pending_ai_card"] = farewell_card
    session["toast"] = {
        "type": "success",
        "title": f"👋 Goodbye {user_name}!",
        "message": "You have successfully logged out."
    }

    flash(f"👋 Goodbye {user_name}! You have successfully logged out.", "info")
    return redirect(url_for("auth.login"))


# ==========================================
# ENTERPRISE USER MANAGEMENT (ADMIN & MANAGER)
# ==========================================
@auth_bp.route("/users")
@login_required
@manager_required
def users_list():
    from database.queries import get_all_users
    with get_db_cursor() as cursor:
        cursor.execute(get_all_users())
        users = cursor.fetchall()
    return render_template("auth/users.html", users=users)


@auth_bp.route("/user/role/<int:target_user_id>", methods=["POST"])
@login_required
@admin_required
def update_role(target_user_id):
    new_role = request.form.get("role", "Analyst").strip()
    if new_role not in ["Admin", "Manager", "Analyst", "Viewer"]:
        flash("Invalid role selected.", "error")
        return redirect(url_for("auth.users_list"))

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("UPDATE Users SET Role = ? WHERE UserID = ?", (new_role, target_user_id))
        log_audit_event("UPDATE_USER_ROLE", f"Updated User #{target_user_id} role to '{new_role}'", user_id=session.get("user_id"))
        flash(f"User role updated to '{new_role}' successfully!", "success")
    except Exception as e:
        flash(f"Failed to update role: {str(e)}", "error")

    return redirect(url_for("auth.users_list"))


@auth_bp.route("/user/toggle/<int:target_user_id>", methods=["POST"])
@login_required
@manager_required
def toggle_user_active(target_user_id):
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("UPDATE Users SET IsActive = CASE WHEN ISNULL(IsActive, 1) = 1 THEN 0 ELSE 1 END WHERE UserID = ?", (target_user_id,))
        log_audit_event("TOGGLE_USER_STATUS", f"Toggled status for User #{target_user_id}", user_id=session.get("user_id"))
        flash("User status updated successfully!", "success")
    except Exception as e:
        flash(f"Failed to update user status: {str(e)}", "error")

    return redirect(url_for("auth.users_list"))

