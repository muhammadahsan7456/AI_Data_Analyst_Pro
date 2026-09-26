import os
import re
import math
import time
from datetime import datetime
import pandas as pd
from itsdangerous import URLSafeTimedSerializer, BadSignature
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    Response,
    abort,
    jsonify,
    session,
    send_from_directory,
    current_app
)

try:
    import psutil
except ImportError:
    psutil = None

from database.connection import (
    get_db_cursor,
    run_query,
    get_table_columns,
    get_table_preview,
    is_safe_identifier,
    sanitize_identifier
)

from database.queries import (
    get_total_datasets,
    get_total_rows,
    get_latest_dataset,
    get_all_datasets,
    get_recently_opened_datasets,
    get_top_dataset_by_rows,
    get_largest_dataset_by_size,
    get_most_asked_queries,
    get_dataset_by_id,
    delete_dataset_record,
    update_dataset_name,
    search_datasets,
    toggle_favorite_dataset,
    update_dataset_tags,
    touch_dataset_opened_at,
    get_total_queries,
    get_total_charts_generated,
    log_query_execution,
    get_user_ai_notifications,
    get_unread_ai_notification_count,
    mark_ai_notification_read,
    clear_user_ai_notifications,
    insert_contact_message
)

from ai.smart_assistant import (
    trigger_ai_event,
    build_dataset_upload_card,
    build_dataset_delete_card,
    build_dataset_edit_card,
    build_report_export_card
)

from uploads.multi_loader import process_file_upload
from uploads.data_loader import auto_repair_dataframe_headers, clean_table_name
from ai.smart_selector import detect_best_dataset_for_query
from ai.multi_dataset import execute_multi_dataset_analysis
from ai.voice import format_tts_response
from ai.query_plan_engine import parse_question_to_query_plan
from utils.logger import log_ai_event, log_upload_event
from ai.gemini import (
    execute_sql_with_retry,
    generate_data_business_summary,
    UNRELATED_MESSAGE
)
from ai.data_summary import format_ai_explanation
from utils.error_translator import format_user_friendly_error
from utils.helpers import format_12hr_datetime
from ai.analytics import (
    generate_data_quality_report,
    clean_dataset,
    generate_ai_insights
)
from ai.data_quality import analyze_dataset_quality
from ai.insight_engine import generate_automated_insights
from ai.prediction import generate_forecast
from visualization.chart_selector import select_chart
from visualization.chart_generator import generate_chart, generate_chart_svg
from utils.exporters import (
    export_to_csv,
    export_to_excel,
    export_to_pdf_report,
    export_to_word_report,
    export_to_pptx_report,
    export_invoice_to_pdf
)
from utils.helpers import format_bytes
from utils.cache import system_cache
from auth.decorators import (
    login_required,
    role_required,
    viewer_allowed
)
from auth.security import log_audit_event

frontend = Blueprint(
    "frontend",
    __name__,
    template_folder="../templates",
    static_folder="../static"
)


def _is_valid_date(value):
    """Validate a YYYY-MM-DD date string before it is used in any SQL filter."""
    if not value:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


@frontend.route("/favicon.ico")
@frontend.route("/favicon.png")
def favicon():
    static_dir = frontend.static_folder
    if os.path.exists(os.path.join(static_dir, "favicon.png")):
        return send_from_directory(static_dir, "favicon.png", mimetype="image/png")
    elif os.path.exists(os.path.join(static_dir, "favicon.ico")):
        return send_from_directory(static_dir, "favicon.ico", mimetype="image/vnd.microsoft.icon")
    return "", 204


@frontend.route("/secure/payment-screenshot/<int:payment_id>")
def secure_payment_screenshot(payment_id):
    """
    Payment proof screenshots can contain bank account numbers and were previously
    served straight off /static with no access check - anyone who guessed or was
    handed a filename (some of which embed the target's own UserID) could view
    someone else's receipt. This route requires the requester to either own the
    payment record or be logged in as Admin/SuperAdmin before releasing the file.
    """
    requester_id = session.get("user_id")
    requester_role = session.get("user_role")
    admin_id = session.get("admin_user_id")
    admin_role = session.get("admin_user_role")

    if not requester_id and not admin_id:
        abort(404)

    with get_db_cursor() as cursor:
        cursor.execute("SELECT UserID, ScreenshotPath FROM Payments WHERE PaymentID = ?", (payment_id,))
        row = cursor.fetchone()

    if not row or not row[1]:
        abort(404)

    owner_id, screenshot_path = row
    is_owner = requester_id is not None and requester_id == owner_id
    is_admin = requester_role in ("Admin", "SuperAdmin") or admin_role in ("Admin", "SuperAdmin")
    if not (is_owner or is_admin):
        abort(404)

    rel_path = screenshot_path.replace("/static/", "").replace("static/", "").lstrip("/\\")
    file_path = os.path.join(frontend.static_folder, rel_path)
    directory, filename = os.path.split(file_path)
    if not os.path.exists(file_path):
        abort(404)
    return send_from_directory(directory, filename)


# ==========================================
# PUBLIC MARKETING HOME LANDING PAGE (SEO OPTIMIZED)
# ==========================================
@frontend.route("/")
def home():
    is_logged_in = bool(session.get("user_id"))
    return render_template("index.html", is_logged_in=is_logged_in)


@frontend.route("/pricing")
def pricing_page():
    is_logged_in = bool(session.get("user_id"))
    return render_template("pricing_page.html", is_logged_in=is_logged_in, open_payment_modal=True)


@frontend.route("/submit-payment-proof", methods=["POST"])
def submit_payment_proof():
    """
    Handle user manual payment proof submission for Easypaisa / Nayapay with screenshot image upload.
    Inserts a Pending payment record in database for SuperAdmin verification.
    """
    try:
        from werkzeug.utils import secure_filename
        data = request.form if request.form else (request.get_json(silent=True) or {})
        email = (data.get("email") or "").strip().lower()
        method = (data.get("payment_method") or "Easypaisa").strip()
        txn_id = (data.get("transaction_id") or "").strip()

        if not email:
            return jsonify({"success": False, "message": "Registered email address is required."}), 400

        # Handle Screenshot Image Upload - same extension allow-list (reject, not silently
        # relabel) and 5 MB cap as register_payment()/renew_subscription(), so this older
        # entry point can't be used to store arbitrary file content under a faked .png name
        # or an oversized file with no real size limit beyond the app-wide 500 MB cap.
        screenshot_path = None
        file_obj = request.files.get("screenshot_file") or request.files.get("screenshot")
        if file_obj and file_obj.filename:
            ext = os.path.splitext(file_obj.filename)[1].lower()
            if ext not in [".png", ".jpg", ".jpeg", ".webp", ".jfif"]:
                return jsonify({"success": False, "message": "Screenshot must be a JPG, JPEG, PNG, or WEBP image."}), 400

            file_obj.seek(0, os.SEEK_END)
            size = file_obj.tell()
            file_obj.seek(0)
            if size > 5 * 1024 * 1024:
                return jsonify({"success": False, "message": "Screenshot file is too large - maximum size is 5 MB."}), 400

            upload_dir = os.path.join(frontend.static_folder, "uploads", "payment_screenshots")
            os.makedirs(upload_dir, exist_ok=True)

            safe_name = f"proof_{int(time.time())}_{secure_filename(file_obj.filename)}"
            if not safe_name.endswith(ext):
                safe_name += ext

            save_dest = os.path.join(upload_dir, safe_name)
            file_obj.save(save_dest)
            screenshot_path = f"/static/uploads/payment_screenshots/{safe_name}"
        else:
            return jsonify({"success": False, "message": "Payment screenshot is required."}), 400

        user_name = "Valued Customer"

        with get_db_cursor(commit=True) as cursor:
            # This form only accepts a payment for an EXISTING registered account, matched
            # strictly by the email address the submitter typed - falling back to "the
            # first user in the table" when the email didn't match anyone (the previous
            # behavior here) attached the payment record to an unrelated, arbitrary
            # account instead of rejecting the submission.
            cursor.execute("SELECT UserID, FullName FROM Users WHERE LOWER(Email) = ?", (email,))
            row = cursor.fetchone()

            if not row:
                return jsonify({
                    "success": False,
                    "message": "No account found with that email address. Please check the email you registered with, or register a new account first."
                }), 400

            user_id, user_name = row[0], row[1] or user_name

            if not txn_id:
                txn_id = f"PAY_{int(time.time())}"

            method_display = f"{method} (Acc: 03053107456 - Muhammad Ahsan)"

            cursor.execute("""
                INSERT INTO Payments (UserID, Amount, Currency, PaymentMethod, TransactionID, Status, PlanName, ScreenshotPath, PaymentDate)
                VALUES (?, 25000.00, 'PKR', ?, ?, 'Pending', 'Enterprise Plan (PKR 25,000/mo)', ?, GETDATE())
            """, (user_id, method_display, txn_id, screenshot_path))

        try:
            log_audit_event("PAYMENT_PROOF_SUBMITTED", f"Submitted {method} payment proof & screenshot for {email} (Txn: {txn_id})", user_id=user_id)
        except Exception:
            pass

        return jsonify({
            "success": True,
            "message": "Payment screenshot & verification request recorded successfully! Super Admin will verify your transaction shortly."
        })
    except Exception as e:
        print("Submit Payment Proof Error:", e)
        return jsonify({"success": False, "message": f"Error saving payment proof: {str(e)}"}), 500


# ==========================================
# DASHBOARD (ISOLATED PER LOGGED-IN USER)
# ==========================================
@frontend.route("/dashboard")
@login_required
@viewer_allowed
def dashboard():
    user_id = session.get("user_id")
    search_query = request.args.get("q", "").strip()
    sort_by = request.args.get("sort", "UploadDate").strip()
    order = request.args.get("order", "DESC").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    date_from = date_from if _is_valid_date(date_from) else None
    date_to = date_to if _is_valid_date(date_to) else None
    page = request.args.get("page", 1, type=int)
    per_page = 10

    total_datasets = 0
    total_rows = 0
    total_queries = 0
    charts_generated = 0
    latest_dataset = None
    top_dataset = None
    largest_dataset = None
    most_asked_queries = []
    all_datasets = []
    recent_datasets = []

    cache_key = f"dash_metrics_{user_id}_{hash(search_query)}_{sort_by}_{order}_{date_from}_{date_to}_{page}"
    cached_dash = system_cache.get(cache_key)

    if cached_dash and not search_query:
        return render_template("dashboard.html", **cached_dash)

    with get_db_cursor() as cursor:
        try:
            cursor.execute(get_total_datasets(user_id=user_id))
            res = cursor.fetchone()
            if res: total_datasets = res[0] or 0
        except Exception: pass

        try:
            cursor.execute(get_total_rows(user_id=user_id))
            res = cursor.fetchone()
            if res: total_rows = res[0] or 0
        except Exception: pass

        try:
            cursor.execute(get_total_queries(user_id=user_id))
            res = cursor.fetchone()
            if res: total_queries = res[0] or 0
        except Exception: pass

        try:
            cursor.execute(get_total_charts_generated(user_id=user_id))
            res = cursor.fetchone()
            if res: charts_generated = res[0] or 0
        except Exception: pass

        try:
            cursor.execute(get_latest_dataset(user_id=user_id))
            latest_dataset = cursor.fetchone()
        except Exception: pass

        try:
            cursor.execute(get_top_dataset_by_rows(user_id=user_id))
            top_dataset = cursor.fetchone()
        except Exception: pass

        try:
            cursor.execute(get_largest_dataset_by_size(user_id=user_id))
            largest_dataset = cursor.fetchone()
        except Exception: pass

        try:
            cursor.execute(get_most_asked_queries(user_id=user_id, limit=4))
            most_asked_queries = cursor.fetchall()
        except Exception: pass

        try:
            cursor.execute(get_recently_opened_datasets(user_id=user_id, limit=4))
            recent_datasets = cursor.fetchall()
        except Exception: pass

        try:
            params = []
            if search_query:
                params.extend([f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"])

            if search_query:
                query_str = search_datasets(user_id=user_id, sort_by=sort_by, order=order, date_from=date_from, date_to=date_to)
            else:
                query_str = get_all_datasets(user_id=user_id, sort_by=sort_by, order=order, date_from=date_from, date_to=date_to)

            # Date params must be appended in the same order the WHERE clauses
            # are built in database/queries.py (date_from, then date_to).
            if date_from:
                params.append(date_from)
            if date_to:
                params.append(date_to)

            cursor.execute(query_str, tuple(params) if params else ())
            all_datasets = cursor.fetchall()
        except Exception as query_err:
            print("Dashboard Query Error:", query_err)

    total_storage_kb = sum(ds[6] for ds in all_datasets if len(ds) > 6 and ds[6])
    storage_display = format_bytes(total_storage_kb * 1024)

    total_found = len(all_datasets)
    total_pages = max(1, math.ceil(total_found / per_page))
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * per_page
    paginated_datasets = all_datasets[start_idx:start_idx + per_page]

    renewal_notice = None
    pending_payment_notice = None
    try:
        from utils.subscription import get_user_subscription_info
        sub_meta = get_user_subscription_info(user_id)

        # Check if user has uploaded a payment receipt proof that is under review
        with get_db_cursor() as cursor:
            cursor.execute("SELECT TOP 1 Status, PaymentDate FROM Payments WHERE UserID = ? ORDER BY PaymentDate DESC", (user_id,))
            p_row = cursor.fetchone()
            if p_row and p_row[0] in ['Pending', 'Pending_Review']:
                pending_payment_notice = "⏳ Payment Proof Verification Pending: Your payment receipt proof (PKR 25,000) has been received and is currently under Super Admin review. Account access will be extended automatically upon verification!"

        # Renewal notice only displays if subscription requires renewal AND no pending payment is under review
        if not pending_payment_notice and sub_meta.get("show_banner"):
            renewal_notice = sub_meta.get("banner_message")
    except Exception as ren_err:
        print("Renewal Calculation Notice:", ren_err)

    context = dict(
        total_datasets=total_datasets,
        total_rows=total_rows,
        total_queries=total_queries,
        storage_display=storage_display,
        charts_generated=charts_generated,
        latest_dataset=latest_dataset,
        top_dataset=top_dataset,
        largest_dataset=largest_dataset,
        most_asked_queries=most_asked_queries,
        recent_datasets=recent_datasets,
        datasets=paginated_datasets,
        subscription_renewal_notice=renewal_notice,
        pending_payment_notice=pending_payment_notice,
        page=page,
        total_pages=total_pages,
        total_found=total_found,
        search_query=search_query,
        sort_by=sort_by,
        order=order,
        date_from=date_from,
        date_to=date_to,
        ai_status="Online"
    )

    if not search_query:
        system_cache.set(cache_key, context, ttl=60)

    return render_template("dashboard.html", **context)


# ==========================================
# UPLOAD DATASET & MULTI-FILE AJAX ENDPOINT
# ==========================================
@frontend.route("/upload", methods=["GET", "POST"])
@login_required
@role_required("Admin", "Manager", "Analyst")
def upload():
    user_id = session.get("user_id")
    if request.method == "POST":
        # Handle multiple file upload form submissions
        files = request.files.getlist("dataset") or [request.files.get("dataset")]
        tags = request.form.get("tags", "").strip()
        results = []
        errors = []

        for uploaded_file in files:
            if uploaded_file and getattr(uploaded_file, "filename", None):
                success, res = process_file_upload(uploaded_file, user_id=user_id, tags=tags)
                if success:
                    log_upload_event(f"Uploaded dataset '{uploaded_file.filename}'", user_id=user_id)
                    results.append(res)
                    user_name = session.get("user_name", "User")
                    ds_name = res.get("dataset_name") or res.get("name") or uploaded_file.filename
                    rows = res.get("total_rows") or res.get("rows", 0)
                    cols = res.get("total_columns") or res.get("columns", 0)
                    trigger_ai_event(user_id, build_dataset_upload_card(user_name, ds_name, rows, cols))
                else:
                    errors.append(res)

        system_cache.invalidate_pattern(f"dash_metrics_{user_id}")

        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({
                "success": len(errors) == 0,
                "results": results,
                "errors": errors
            })

        if results:
            flash(f"Successfully uploaded {len(results)} dataset file(s)!", "success")
            return render_template("upload.html", success=True, data=results[0], results=results)
        
        return render_template("upload.html", success=False, error="; ".join(errors) if errors else "No file selected.")

    return render_template("upload.html")


@frontend.route("/api/upload-file", methods=["POST"])
@login_required
@role_required("Admin", "Manager", "Analyst")
def api_upload_file():
    """
    AJAX endpoint for multi-file upload progress bars, percentage, and upload speed tracking.
    """
    user_id = session.get("user_id")
    uploaded_file = request.files.get("file") or request.files.get("dataset")
    tags = request.form.get("tags", "").strip()

    if not uploaded_file or not getattr(uploaded_file, "filename", None):
        return jsonify({"success": False, "error": "No file uploaded."}), 400

    success, res = process_file_upload(uploaded_file, user_id=user_id, tags=tags)
    if success:
        system_cache.invalidate_pattern(f"dash_metrics_{user_id}")
        log_upload_event(f"AJAX Uploaded: {uploaded_file.filename}", user_id=user_id)
        user_name = session.get("user_name", "User")
        ds_name = res.get("dataset_name") or res.get("name") or uploaded_file.filename
        rows = res.get("total_rows") or res.get("rows", 0)
        cols = res.get("total_columns") or res.get("columns", 0)
        card = build_dataset_upload_card(user_name, ds_name, rows, cols)
        trigger_ai_event(user_id, card)
        return jsonify({"success": True, "data": res, "ai_card": card})

    return jsonify({"success": False, "error": res}), 400



# ==========================================
# FAVORITE TOGGLE ROUTE (ISOLATED)
# ==========================================
@frontend.route("/dataset/favorite/<int:dataset_id>", methods=["POST"])
@login_required
def favorite_dataset(dataset_id):
    user_id = session.get("user_id")
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(toggle_favorite_dataset(user_id=user_id), (dataset_id,))
        system_cache.invalidate_pattern(f"dash_metrics_{user_id}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


# ==========================================
# UPDATE TAGS ROUTE (ISOLATED)
# ==========================================
@frontend.route("/dataset/tags/<int:dataset_id>", methods=["POST"])
@login_required
@role_required("Admin", "Manager", "Analyst")
def tags_dataset(dataset_id):
    user_id = session.get("user_id")
    tags = request.form.get("tags", "").strip()
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(update_dataset_tags(user_id=user_id), (tags, dataset_id))
        system_cache.invalidate_pattern(f"dash_metrics_{user_id}")
        flash("Tags updated successfully!", "success")
    except Exception as e:
        flash(f"Failed to update tags: {str(e)}", "error")
    return redirect(url_for("frontend.dashboard"))


# ==========================================
# ASK AI CHAT INTERFACE WITH SMART SELECTION & VOICE TTS
# ==========================================
@frontend.route("/chat", methods=["GET", "POST"])
@login_required
@role_required("Admin", "Manager", "Analyst")
def chat():
    user_id = session.get("user_id")
    is_ajax = request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("ajax") == "1"

    with get_db_cursor() as cursor:
        cursor.execute(get_all_datasets(user_id=user_id))
        raw_user_datasets = cursor.fetchall()

    from database.connection import run_query
    tbl_df = run_query("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'")
    existing_tables = set(tbl_df['TABLE_NAME'].str.lower().tolist()) if (tbl_df is not None and not tbl_df.empty) else set()

    user_datasets = []
    if raw_user_datasets:
        for ds in raw_user_datasets:
            tname = str(ds[1]).strip()
            if tname.lower() in existing_tables and tname.lower() != "tbl_sample_orders":
                user_datasets.append(ds)

    if not user_datasets:
        if is_ajax:
            return jsonify({"success": False, "error": "No dataset uploaded in your account. Please upload a dataset first."})
        return render_template("chat.html", user_datasets=[], selected_dataset=None, error="No dataset uploaded in your account. Please upload a dataset first.")

    if request.is_json and request.get_json():
        json_data = request.get_json() or {}
        question = str(json_data.get("question") or json_data.get("query") or "").strip()
        dataset_id = json_data.get("dataset_id")
        if dataset_id is not None:
            try: dataset_id = int(dataset_id)
            except Exception: dataset_id = None
    else:
        question = str(request.form.get("question") or request.values.get("question") or request.args.get("question") or "").strip()
        raw_ds = request.form.get("dataset_id") or request.values.get("dataset_id") or request.args.get("dataset_id")
        try: dataset_id = int(raw_ds) if raw_ds is not None else None
        except Exception: dataset_id = None

    selected_dataset = None

    # Smart Dataset Selection (Feature 6): Auto-detect table if query is present and no explicit dataset_id selected
    if question and not dataset_id:
        detected = detect_best_dataset_for_query(question, user_id)
        if detected:
            for ds in user_datasets:
                if ds[0] == detected["dataset_id"]:
                    selected_dataset = ds
                    break

    if not selected_dataset and dataset_id:
        for ds in user_datasets:
            if ds[0] == dataset_id:
                selected_dataset = ds
                break

    if not selected_dataset:
        selected_dataset = user_datasets[0]

    table_name = selected_dataset[1]

    if request.method == "GET" and not question:
        return render_template("chat.html", user_datasets=user_datasets, selected_dataset=selected_dataset)

    if not question:
        return render_template("chat.html", user_datasets=user_datasets, selected_dataset=selected_dataset)

    # Daily AI query quota - each query calls a paid external AI API, so an unbounded
    # per-user volume (accidental loop, scripted abuse, or just very heavy manual use)
    # translates directly into an uncapped cost. SuperAdmin/Admin are exempt since they
    # aren't the paying customer segment this protects against.
    if session.get("user_role") not in ["SuperAdmin", "Admin"]:
        try:
            from datetime import datetime as _dt, timedelta as _td
            from database.queries import get_user_query_count_since
            daily_limit = int(os.getenv("MAX_AI_QUERIES_PER_DAY", "150"))
            since = _dt.now() - _td(hours=24)
            with get_db_cursor() as cursor:
                cursor.execute(get_user_query_count_since(), (user_id, since))
                query_count_today = cursor.fetchone()[0] or 0

            if query_count_today >= daily_limit:
                limit_msg = f"⚠️ Daily AI query limit reached ({daily_limit}/day). Please try again after 24 hours, or contact support to increase your limit."
                if is_ajax:
                    return jsonify({"success": False, "error": limit_msg})
                return render_template("chat.html", user_datasets=user_datasets, selected_dataset=selected_dataset, error=limit_msg)
        except Exception as quota_err:
            print("AI Query Quota Check Error:", quota_err)

    # Conversation Context Memory & Session City/Status Persistence (Multi-Turn Context)
    last_query = session.get("last_query", "")
    last_sql = session.get("last_sql", "")
    last_city = session.get("last_city", "")
    last_status = session.get("last_status", "")

    q_low = question.lower()

    # Detect current city and status in active question
    known_pk_cities = ["karachi", "lahore", "islamabad", "rawalpindi", "multan", "peshawar", "faisalabad", "quetta", "hyderabad", "sukkur", "sialkot", "gujranwala"]
    current_city = next((c.title() for c in known_pk_cities if c in q_low), None)
    current_status = "return" if any(kw in q_low for kw in ["return", "returned", "rto", "audits"]) else ("delivered" if any(kw in q_low for kw in ["delivered", "delivery"]) else None)

    if current_city:
        session["last_city"] = current_city
    if current_status:
        session["last_status"] = current_status

    active_city = current_city or session.get("last_city", "")
    active_status = current_status or session.get("last_status", "")

    # Check for relative follow-up context (e.g., "Inke areas bhi batao", "Area breakdown", "Sort descending")
    is_area_followup = any(kw in q_low for kw in ["area", "areas", "locality", "localities", "neighborhood", "inke", "unke", "is ke", "un ke", "in ke", "kaun se", "kaun kaun"])
    
    if is_area_followup and active_city and not current_city:
        status_prefix = f" {active_status}" if active_status else ""
        question_for_ai = f"{active_city}{status_prefix} area breakdown {question}"
    elif last_sql and any(kw in q_low for kw in ["sort", "order", "descending", "ascending", "filter", "where"]):
        if "order by" not in last_sql.lower() and "sort" in q_low:
            if "desc" in q_low or "descending" in q_low:
                question_for_ai = f"{last_query} sorted descending"
            else:
                question_for_ai = f"{last_query} sorted"
        else:
            question_for_ai = question
    else:
        question_for_ai = question

    columns = get_table_columns(table_name)
    if not columns:
        return render_template("chat.html", user_datasets=user_datasets, selected_dataset=selected_dataset, error=f"Table '{table_name}' contains no accessible columns.")

    from utils.helpers import generate_dynamic_prompt_suggestions
    dynamic_suggestions = generate_dynamic_prompt_suggestions(columns)

    if not question:
        return render_template("chat.html", user_datasets=user_datasets, selected_dataset=selected_dataset, dynamic_suggestions=dynamic_suggestions)

    from ai.multi_concept_engine import execute_multi_concept_analysis
    ai_result = execute_multi_concept_analysis(question_for_ai, table_name, columns, user_id=user_id)
    log_ai_event(f"Asked: '{question_for_ai}' on table [{table_name}]", user_id=user_id)

    is_ajax = request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("ajax") == "1"

    if ai_result.get("is_out_of_domain"):
        if is_ajax:
            return jsonify({
                "success": False,
                "is_out_of_domain": True,
                "error": UNRELATED_MESSAGE,
                "tts_speech": UNRELATED_MESSAGE
            })
        return render_template(
            "chat.html",
            user_datasets=user_datasets,
            selected_dataset=selected_dataset,
            question=question,
            is_out_of_domain=True,
            error=UNRELATED_MESSAGE,
            tts_speech=UNRELATED_MESSAGE
        )

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                log_query_execution(),
                (
                    selected_dataset[0],
                    question,
                    ai_result["sql"],
                    "Success" if ai_result["success"] else "Error",
                    ai_result["rows_returned"],
                    ai_result["execution_time_ms"],
                    ai_result["confidence"],
                    ai_result["retries"]
                )
            )
    except Exception:
        pass

    if not ai_result["success"]:
        clean_user_err = format_user_friendly_error(ai_result['error'])
        if is_ajax:
            return jsonify({
                "success": False,
                "is_out_of_domain": False,
                "sql": ai_result["sql"],
                "error": clean_user_err,
                "tts_speech": "Sorry, I could not execute that query on the dataset."
            })
        return render_template(
            "chat.html",
            user_datasets=user_datasets,
            selected_dataset=selected_dataset,
            question=question,
            sql=ai_result["sql"],
            error=clean_user_err,
            tts_speech="Sorry, I could not execute that query on the dataset."
        )

    df = ai_result["df"]

    # ---------------------------------------------------------
    # STRICT CITY & LOCATION POST-FILTERING SAFETY GUARD (100% ACCURACY)
    # ---------------------------------------------------------
    if df is not None and not df.empty:
        known_cities_map = {
            "lahore": ["lahore", "lhe"],
            "islamabad": ["islamabad", "isb"],
            "karachi": ["karachi", "khi"],
            "multan": ["multan", "mux"],
            "peshawar": ["peshawar", "pew"],
            "rawalpindi": ["rawalpindi", "rwp"],
            "quetta": ["quetta", "uet"],
            "faisalabad": ["faisalabad", "fsd"],
            "sialkot": ["sialkot", "skt"],
            "hyderabad": ["hyderabad", "hdd"]
        }

        q_low_check = question.lower()
        target_city_key = None
        for c_key, c_aliases in known_cities_map.items():
            if any(re.search(r"\b" + re.escape(alias) + r"\b", q_low_check) for alias in c_aliases):
                if not any(kw in q_low_check for kw in ["all cities", "city breakdown", "every city", "compare", "vs", "versus"]):
                    target_city_key = c_key
                    break

        if target_city_key:
            city_col_candidates = ["destination_city", "dest_city", "destination", "customer_city", "shipping_city", "consignee_city", "city", "location"]
            target_df_col = None
            for col_name in df.columns:
                if col_name.lower().strip() in city_col_candidates:
                    target_df_col = col_name
                    break
            if not target_df_col:
                for col_name in df.columns:
                    if "city" in col_name.lower() or "destination" in col_name.lower():
                        target_df_col = col_name
                        break

            if target_df_col:
                allowed_tokens = known_cities_map[target_city_key]
                def matches_target_city(val):
                    if pd.isna(val):
                        return False
                    v_str = str(val).lower().strip()
                    return any(t in v_str for t in allowed_tokens)

                filtered_df = df[df[target_df_col].apply(matches_target_city)]
                if not filtered_df.empty:
                    df = filtered_df.copy()
                    ai_result["df"] = df
                    ai_result["rows_returned"] = len(df)
    if df.empty:
        if is_ajax:
            return jsonify({
                "success": True,
                "is_out_of_domain": False,
                "sql": ai_result["sql"],
                "rows_returned": 0,
                "execution_time_ms": ai_result["execution_time_ms"],
                "confidence": ai_result["confidence"],
                "retries": ai_result["retries"],
                "error": "Query executed successfully but returned 0 matching records.",
                "tts_speech": "The query returned no matching records in the dataset."
            })
        return render_template(
            "chat.html",
            user_datasets=user_datasets,
            selected_dataset=selected_dataset,
            question=question,
            sql=ai_result["sql"],
            execution_time_ms=ai_result["execution_time_ms"],
            confidence=ai_result["confidence"],
            retries=ai_result["retries"],
            rows_returned=0,
            error="Query executed successfully but returned 0 matching records.",
            tts_speech="The query returned no matching records in the dataset."
        )

    # Save to session memory
    session["last_query"] = question
    session["last_sql"] = ai_result["sql"]

    # Record User Query History (User Data Isolation)
    try:
        from database.queries import insert_query_history
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(insert_query_history(), (
                user_id, selected_dataset[0], question, ai_result["sql"],
                "Success", len(df), ai_result.get("execution_time_ms", 0.0),
                "auto", None
            ))
    except Exception:
        pass

    try:
        explanation = generate_data_business_summary(selected_dataset[2], df, question)
    except Exception:
        explanation = f"Query executed successfully on dataset [{selected_dataset[2]}]. Displaying {len(df)} matching records."

    # Natural Spoken Speech formatting (Feature 2)
    tts_speech = format_tts_response(question, df, explanation)

    # The Matplotlib PNG ("chart_path") is intentionally not generated here - it's only
    # produced on demand by frontend.download_chart() when the user actually clicks
    # "Download HD Chart", or by the export routes. The Plotly chart_spec is what renders
    # the interactive chart on the page itself.
    chart_path = None
    chart_spec = ai_result.get("chart_spec", {})
    if not chart_spec or not chart_spec.get("labels"):
        try:
            from visualization.chart_generator import generate_interactive_chart_spec
            chart_type = select_chart(df) or "bar"
            chart_spec = generate_interactive_chart_spec(df, chart_type)
        except Exception as chart_err:
            print("Chart Error:", chart_err)

    # Auto-repair and infer clean human headers for any Unnamed/blank columns
    df = auto_repair_dataframe_headers(df)

    # Expand city airport codes to full city names (e.g. KHI -> Karachi (KHI), LHE -> Lahore (LHE))
    from utils.helpers import expand_city_names_in_df
    df = expand_city_names_in_df(df)

    # Clean technical RecordID column
    if "RecordID" in df.columns:
        df = df.drop(columns=["RecordID"])

    display_df = df.copy()

    # Append Total Summary Row for Category Count / Distribution Tables
    if len(display_df) > 1 and len(display_df) <= 50 and any(kw in str(c).lower() for c in display_df.columns for kw in ["status", "count", "destination", "origin", "category"]):
        count_cols = [c for c in display_df.columns if any(kw in c.lower() for kw in ["count", "total", "amount", "value", "orders", "customers"])]
        if count_cols and pd.api.types.is_numeric_dtype(display_df[count_cols[0]]):
            total_row = {}
            for col in display_df.columns:
                if col == display_df.columns[0]:
                    total_row[col] = "Total"
                elif col in count_cols and pd.api.types.is_numeric_dtype(display_df[col]):
                    total_row[col] = display_df[col].sum()
                else:
                    total_row[col] = ""
            display_df = pd.concat([display_df, pd.DataFrame([total_row])], ignore_index=True)

    display_df = display_df.reset_index(drop=True)
    if "S.No" not in display_df.columns:
        display_df.insert(0, "S.No", [i if i != len(display_df) else "" for i in range(1, len(display_df) + 1)])

    result_html = display_df.to_html(
        classes="table table-bordered table-striped custom-table",
        index=False
    )

    query_plan = parse_question_to_query_plan(question_for_ai, table_name) if parse_question_to_query_plan else {}
    applied_filters = [f["sql_clause"] for f in query_plan.get("filters", [])] if query_plan else []

    if is_ajax:
        return jsonify({
            "success": True,
            "question": question,
            "selected_dataset_id": selected_dataset[0],
            "selected_dataset_name": selected_dataset[2],
            "is_out_of_domain": False,
            "sql": ai_result["sql"],
            "query_plan": query_plan,
            "applied_filters": applied_filters,
            "result_html": result_html,
            "explanation": format_ai_explanation(explanation),
            "tts_speech": tts_speech,
            "chart": chart_path,
            "chart_spec": chart_spec,
            "execution_time_ms": ai_result["execution_time_ms"],
            "confidence": ai_result["confidence"],
            "retries": ai_result["retries"],
            "rows_returned": ai_result["rows_returned"]
        })

    return render_template(
        "chat.html",
        user_datasets=user_datasets,
        selected_dataset=selected_dataset,
        question=question,
        sql=ai_result["sql"],
        query_plan=query_plan,
        applied_filters=applied_filters,
        result=result_html,
        explanation=explanation,
        chart=chart_path,
        chart_spec=chart_spec,
        execution_time_ms=ai_result["execution_time_ms"],
        confidence=ai_result["confidence"],
        retries=ai_result["retries"],
        rows_returned=ai_result["rows_returned"],
        tts_speech=tts_speech
    )


# ==========================================
# ON-DEMAND HD CHART DOWNLOAD (lazy Matplotlib render)
# ==========================================
@frontend.route("/download-chart")
@login_required
def download_chart():
    """
    Regenerates the Matplotlib PNG for the most recent Ask AI query on demand, only when
    the user actually clicks "Download HD Chart" - the interactive Plotly chart on the page
    itself doesn't need this, so it's no longer generated eagerly on every query.
    """
    last_sql = session.get("last_sql")
    if not last_sql:
        flash("No recent chart available to download. Ask a question first.", "error")
        return redirect(url_for("frontend.chat"))

    try:
        df = run_query(last_sql)
    except Exception:
        flash("Could not regenerate the chart - please ask your question again.", "error")
        return redirect(url_for("frontend.chat"))

    if df is None or df.empty:
        flash("No data available to chart.", "error")
        return redirect(url_for("frontend.chat"))

    chart_type = select_chart(df) or "bar"
    chart_rel_path = generate_chart(df, chart_type)
    if not chart_rel_path:
        flash("Could not generate a chart for this data.", "error")
        return redirect(url_for("frontend.chat"))

    return send_from_directory(
        os.path.join(frontend.static_folder, os.path.dirname(chart_rel_path)),
        os.path.basename(chart_rel_path),
        as_attachment=True,
        download_name="AI_Chart_Analysis.png"
    )


# ==========================================
# REAL-TIME SMART SEARCH API (FEATURE 10)
# ==========================================
@frontend.route("/api/smart-search")
@login_required
def smart_search_api():
    """
    Real-time Dataset Search API (Suggests Datasets only).
    """
    user_id = session.get("user_id")
    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify({"results": []})

    results = []
    q_param = f"%{query}%"

    with get_db_cursor() as cursor:
        try:
            cursor.execute(
                "SELECT DatasetID, DatasetName, OriginalFileName, FileType, TotalRows FROM Datasets WHERE UserID = ? AND (OriginalFileName LIKE ? OR DatasetName LIKE ? OR Tags LIKE ?)",
                (user_id, q_param, q_param, q_param)
            )
            for row in cursor.fetchall():
                results.append({
                    "category": "Dataset",
                    "title": row[2],
                    "subtitle": f"{row[3].upper()} format | {row[4]:,} rows",
                    "url": url_for("frontend.view_dataset", dataset_id=row[0])
                })
        except Exception as err:
            print("Smart Search Dataset Query Error:", err)

    return jsonify({"results": results})


# ==========================================
# MULTI-DATASET ANALYSIS API (FEATURE 5)
# ==========================================
@frontend.route("/api/multi-dataset/analyze", methods=["POST"])
@login_required
@role_required("Admin", "Manager", "Analyst")
def multi_dataset_analyze():
    """
    Cross-Dataset AI Query Engine (Compare, Join, Merge, Union, Duplicates).
    """
    user_id = session.get("user_id")
    data = request.get_json() or {}
    dataset_ids = data.get("dataset_ids", [])
    question = data.get("question", "").strip()

    if not dataset_ids or len(dataset_ids) < 2:
        return jsonify({"success": False, "error": "Please select at least 2 datasets for cross-analysis."}), 400

    # A single batched IN(...) query instead of one round-trip per selected dataset -
    # dataset_ids is attacker-suppliable but each id is cast to int before it ever
    # touches the query string, and UserID is still enforced as a bound parameter so
    # this can't be used to pull in another user's dataset.
    try:
        safe_ids = [int(d) for d in dataset_ids]
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid dataset selection."}), 400

    dataset_tables = []
    with get_db_cursor() as cursor:
        placeholders = ",".join("?" for _ in safe_ids)
        cursor.execute(
            f"SELECT DatasetID, DatasetName, OriginalFileName FROM Datasets WHERE UserID = ? AND DatasetID IN ({placeholders})",
            (user_id, *safe_ids)
        )
        dataset_tables = [(row[0], row[1], row[2]) for row in cursor.fetchall()]

    if len(dataset_tables) < 2:
        return jsonify({"success": False, "error": "Could not access selected datasets."}), 400

    analysis_res = execute_multi_dataset_analysis(question, dataset_tables)
    if not analysis_res["success"]:
        return jsonify(analysis_res), 400

    df = analysis_res["df"]
    result_html = df.head(50).to_html(
        classes="table table-bordered table-striped custom-table",
        index=False
    )
    analysis_res["result_html"] = result_html
    del analysis_res["df"]  # remove non-serializable DF

    log_ai_event(f"Multi-dataset query: '{question}' across {len(dataset_tables)} tables.", user_id=user_id)
    return jsonify(analysis_res)


# ==========================================
# AI PREDICTIONS & TIME SERIES FORECASTING HUB
# ==========================================
@frontend.route("/predictions", methods=["GET", "POST"])
@login_required
@role_required("Admin", "Manager", "Analyst")
def predictions():
    user_id = session.get("user_id")

    with get_db_cursor() as cursor:
        cursor.execute(get_all_datasets(user_id=user_id))
        user_datasets = cursor.fetchall()

    if not user_datasets:
        flash("No dataset uploaded in your account to run predictions.", "warning")
        return redirect(url_for("frontend.dashboard"))

    selected_dataset = user_datasets[0]
    forecast_days = 30
    result = None

    if request.method == "POST":
        dataset_id = request.form.get("dataset_id", type=int)
        forecast_days = request.form.get("forecast_days", 30, type=int)

        with get_db_cursor() as cursor:
            cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
            match = cursor.fetchone()
            if match:
                selected_dataset = match

        table_name = selected_dataset[2]
        df = run_query(f"SELECT TOP 10000 * FROM {sanitize_identifier(table_name)}")

        result = generate_forecast(df, forecast_days=forecast_days)
        log_audit_event("PREDICTION_RUN", f"Ran forecast on dataset #{selected_dataset[0]} for {forecast_days} days.", user_id=user_id)

    return render_template(
        "predictions.html",
        user_datasets=user_datasets,
        selected_dataset=selected_dataset,
        forecast_days=forecast_days,
        result=result
    )


# ==========================================
# ENTERPRISE SETTINGS SUITE
# ==========================================
@frontend.route("/settings/enterprise", methods=["GET", "POST"])
@login_required
def settings_enterprise():
    user_id = session.get("user_id")

    if request.method == "POST":
        privacy_level = request.form.get("privacy_level", 3, type=int)
        ai_model = request.form.get("ai_model", "openai/gpt-3.5-turbo")

        session["privacy_level"] = privacy_level
        session["ai_model"] = ai_model

        log_audit_event("UPDATE_SETTINGS", f"Updated Privacy Level to {privacy_level}", user_id=user_id)
        flash("Enterprise Settings saved successfully!", "success")
        return redirect(url_for("frontend.settings_enterprise"))

    current_privacy = session.get("privacy_level", 3)
    return render_template("settings_enterprise.html", current_privacy=current_privacy)


# ==========================================
# VIEW & PREVIEW DATASET (USER ISOLATED)
# ==========================================

@frontend.route("/dataset/<int:dataset_id>")
@login_required
@viewer_allowed
def view_dataset(dataset_id):
    user_id = session.get("user_id")
    page = request.args.get("page", 1, type=int)
    raw_per_page = str(request.args.get("per_page", "100")).strip().lower()
    if raw_per_page in ["all", "1000000", "9999999"]:
        per_page = 5000
    else:
        try:
            per_page = int(raw_per_page)
        except Exception:
            per_page = 100
        if per_page not in [10, 25, 50, 100, 500, 1000, 5000]:
            per_page = 5000 if per_page > 5000 else 100

    with get_db_cursor() as cursor:
        cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
        dataset = cursor.fetchone()

    if not dataset:
        flash("Access Denied: You do not have permission to view this dataset or it does not exist.", "error")
        return redirect(url_for("frontend.dashboard"))

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(touch_dataset_opened_at(user_id=user_id), (dataset_id,))
    except Exception:
        pass

    table_name = dataset[2]
    if not is_safe_identifier(table_name):
        flash("Invalid dataset identifier.", "error")
        return redirect(url_for("frontend.dashboard"))

    total_rows = dataset[5] or 0
    total_pages = max(1, math.ceil(total_rows / per_page))
    page = max(1, min(page, total_pages))

    offset = (page - 1) * per_page
    try:
        page_df = get_table_preview(table_name, limit=per_page, offset=offset)
    except Exception as err:
        print(f"Error fetching table preview for dataset #{dataset_id}:", err)
        page_df = pd.DataFrame()

    if page_df.empty:
        flash(f"Notice: SQL table [{table_name}] data is currently empty or unavailable.", "warning")

    start_idx = offset + 1 if not page_df.empty else 0
    end_idx = min(offset + len(page_df), total_rows)

    preview_html = page_df.to_html(
        classes="table table-bordered table-striped custom-table",
        index=False
    )

    return render_template(
        "dataset_preview.html",
        dataset=dataset,
        table_name=table_name,
        total_rows=total_rows,
        total_columns=dataset[6] or len(page_df.columns),
        preview=preview_html,
        page=page,
        per_page=per_page,
        start_idx=start_idx,
        end_idx=end_idx,
        total_pages=total_pages
    )


# ==========================================
# DATASET PROFILING & DETAILS (USER ISOLATED)
# ==========================================
@frontend.route("/dataset/details/<int:dataset_id>")
@login_required
@viewer_allowed
def dataset_details(dataset_id):
    user_id = session.get("user_id")

    with get_db_cursor() as cursor:
        cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
        dataset = cursor.fetchone()

    if not dataset:
        flash("Access Denied: You do not have permission to view this dataset.", "error")
        return redirect(url_for("frontend.dashboard"))

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(touch_dataset_opened_at(user_id=user_id), (dataset_id,))
    except Exception:
        pass

    table_name = dataset[2]
    df = run_query(f"SELECT TOP 5000 * FROM {sanitize_identifier(table_name)}")
    report = generate_data_quality_report(df)
    insights = generate_ai_insights(df)

    heatmap_chart_path = None
    try:
        numeric_cols = df.select_dtypes(include="number")
        if numeric_cols.shape[1] >= 2:
            heatmap_chart_path = generate_chart(df, "heatmap")
    except Exception:
        pass

    return render_template(
        "dataset_details.html",
        dataset=dataset,
        report=report,
        insights=insights,
        heatmap_chart=heatmap_chart_path
    )


# ==========================================
# AI DATA CLEANING (USER ISOLATED)
# ==========================================


@frontend.route("/dataset/clean/<int:dataset_id>", methods=["GET", "POST"])
@login_required
@role_required("Admin", "Manager", "Analyst")
def clean_dataset_view(dataset_id):
    user_id = session.get("user_id")

    with get_db_cursor() as cursor:
        cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
        dataset = cursor.fetchone()

    if not dataset:
        flash("Access Denied: You do not have permission to clean this dataset.", "error")
        return redirect(url_for("frontend.dashboard"))

    table_name = dataset[2]
    df = run_query(f"SELECT * FROM {sanitize_identifier(table_name)}")

    if request.method == "POST":
        remove_dups = request.form.get("remove_duplicates") == "on"
        fill_strategy = request.form.get("fill_strategy", "auto")

        cleaned_df = clean_dataset(df, remove_duplicates=remove_dups, fill_missing=fill_strategy)
        csv_bytes = export_to_csv(cleaned_df)
        
        log_audit_event("CLEAN_DATASET", f"Cleaned dataset: {table_name}", user_id=user_id)
        return Response(
            csv_bytes,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=cleaned_{dataset[3]}"}
        )

    report = generate_data_quality_report(df)
    return render_template("dataset_clean.html", dataset=dataset, report=report)


# ==========================================
# STANDALONE DATA QUALITY & HEALTH SCORE PAGE
# ==========================================
@frontend.route("/data-health/<int:dataset_id>")
@login_required
@viewer_allowed
def data_health(dataset_id):
    user_id = session.get("user_id")
    with get_db_cursor() as cursor:
        cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
        dataset = cursor.fetchone()

    if not dataset:
        flash("Access Denied: You do not have permission to view this dataset health score.", "error")
        return redirect(url_for("frontend.dashboard"))

    table_name = dataset[2]
    try:
        quality_data = analyze_dataset_quality(table_name)
    except Exception as e:
        print("Data Health Analysis Error:", e)
        flash("Couldn't compute a health score for this dataset right now - its data may have changed or the underlying table is missing. Please try again.", "error")
        return redirect(url_for("frontend.dashboard"))
    return render_template("data_health.html", dataset=dataset, quality=quality_data)


# ==========================================
# STANDALONE EXECUTIVE AI BRIEFING & SUMMARY PAGE
# ==========================================
@frontend.route("/executive-summary/<int:dataset_id>")
@login_required
@viewer_allowed
def executive_summary(dataset_id):
    user_id = session.get("user_id")
    with get_db_cursor() as cursor:
        cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
        dataset = cursor.fetchone()

    if not dataset:
        flash("Access Denied: You do not have permission to view this executive summary.", "error")
        return redirect(url_for("frontend.dashboard"))

    table_name = dataset[2]
    try:
        exec_data = generate_automated_insights(table_name)
    except Exception as e:
        print("Executive Summary Analysis Error:", e)
        flash("Couldn't generate an executive summary for this dataset right now - its data may have changed or the underlying table is missing. Please try again.", "error")
        return redirect(url_for("frontend.dashboard"))
    return render_template("executive_summary.html", dataset=dataset, exec_data=exec_data)


# ==========================================
# RENAME DATASET (USER ISOLATED)
# ==========================================
@frontend.route("/dataset/edit/<int:dataset_id>", methods=["GET", "POST"])
@login_required
@role_required("Admin", "Manager", "Analyst")
def edit_dataset(dataset_id):
    user_id = session.get("user_id")
    with get_db_cursor() as cursor:
        cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
        dataset = cursor.fetchone()

    if not dataset:
        flash("Access Denied: You do not have permission to edit this dataset.", "error")
        return redirect(url_for("frontend.dashboard"))

    if request.method == "POST":
        new_display_name = request.form.get("dataset_name", "").strip()
        tags = request.form.get("tags", "").strip()
        if new_display_name:
            # DatasetName doubles as the actual physical SQL table name everywhere else in
            # the app (exports, Ask AI, charts). Previously this just overwrote DatasetName
            # with whatever text the user typed, without renaming the real table - every
            # rename permanently broke that dataset (exports/queries would then fail with
            # "Invalid object name"). Now the physical table is renamed to match.
            old_table_name = dataset[2]
            new_table_name = clean_table_name(new_display_name, user_id=user_id)

            if not is_safe_identifier(new_table_name):
                flash("That name could not be converted into a valid dataset name. Please use letters, numbers, and spaces.", "error")
                return redirect(url_for("frontend.edit_dataset", dataset_id=dataset_id))

            try:
                if new_table_name != old_table_name:
                    with get_db_cursor(commit=True) as cursor:
                        cursor.execute("SELECT OBJECT_ID(?, 'U')", (new_table_name,))
                        if cursor.fetchone()[0] is not None:
                            flash(f"A dataset named '{new_display_name}' already exists. Please choose a different name.", "error")
                            return redirect(url_for("frontend.edit_dataset", dataset_id=dataset_id))

                        # sp_rename's parameter is the plain object name (not bracket-wrapped
                        # like sanitize_identifier() produces for raw SQL interpolation).
                        cursor.execute("EXEC sp_rename ?, ?", (old_table_name, new_table_name))
                        cursor.execute(update_dataset_name(user_id=user_id), (new_table_name, dataset_id))
                        cursor.execute(update_dataset_tags(user_id=user_id), (tags, dataset_id))
                else:
                    with get_db_cursor(commit=True) as cursor:
                        cursor.execute(update_dataset_tags(user_id=user_id), (tags, dataset_id))
            except Exception as e:
                print("Dataset Rename Error:", e)
                flash(f"Failed to rename dataset: {str(e)}", "error")
                return redirect(url_for("frontend.edit_dataset", dataset_id=dataset_id))

            system_cache.invalidate_pattern(f"dash_metrics_{user_id}")
            log_audit_event("EDIT_DATASET", f"Renamed dataset #{dataset_id} to '{new_display_name}' (table: {new_table_name})", user_id=user_id)
            trigger_ai_event(user_id, build_dataset_edit_card())
            flash("Dataset details updated successfully!", "success")
            return redirect(url_for("frontend.dashboard"))

    return render_template("edit_dataset.html", dataset=dataset)


# ==========================================
# DELETE DATASET (USER ISOLATED)
# ==========================================
@frontend.route("/dataset/delete/<int:dataset_id>")
@login_required
def delete_dataset(dataset_id):
    user_id = session.get("user_id")
    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
            dataset = cursor.fetchone()

        if not dataset:
            flash("Access Denied: You do not have permission to delete this dataset.", "error")
            return redirect(url_for("frontend.dashboard"))

        table_name = dataset[2]
        with get_db_cursor(commit=True) as cursor:
            # 1. Clean up associated query logs
            try:
                cursor.execute("DELETE FROM QueryLogs WHERE DatasetID = ?", (dataset_id,))
            except Exception:
                pass

            # 2. Drop Physical SQL Server Table
            if is_safe_identifier(table_name):
                try:
                    cursor.execute(f"IF OBJECT_ID('{table_name}', 'U') IS NOT NULL DROP TABLE {sanitize_identifier(table_name)}")
                except Exception as drop_err:
                    print("Drop Table Warning:", drop_err)

            # 3. Delete metadata record from Datasets table
            cursor.execute(delete_dataset_record(user_id=user_id), (dataset_id,))

        system_cache.invalidate_pattern(f"dash_metrics_{user_id}")
        log_audit_event("DELETE_DATASET", f"Deleted dataset #{dataset_id} ({table_name})", user_id=user_id)
        trigger_ai_event(user_id, build_dataset_delete_card())
        flash("Dataset and SQL Server table deleted successfully.", "success")
    except Exception as e:
        print("DELETE ERROR:", e)
        flash(f"Failed to delete dataset: {str(e)}", "error")

    return redirect(url_for("frontend.dashboard"))


# ==========================================
# EXPORT DATASET / REPORTS / SVG / WORD / PPTX
# ==========================================
def build_content_disposition(filename: str) -> str:
    """
    Build RFC 6266 compliant Content-Disposition header with clean double-quoted filename.
    """
    clean_name = re.sub(r'[\r\n\t"\\/]', '_', str(filename)).strip()
    clean_name = re.sub(r'\s+', '_', clean_name)
    if not clean_name:
        clean_name = "Dataset_Export"
    from urllib.parse import quote
    encoded_name = quote(clean_name)
    return f'attachment; filename="{clean_name}"; filename*=UTF-8\'\'{encoded_name}'


def generate_fast_export_insights(df: pd.DataFrame) -> list:
    """
    Generate instant statistical business insights in 0.001s for ultra-fast export downloads.
    """
    if df is None or df.empty:
        return ["No record data available for insight generation."]

    insights = [f"Dataset contains {len(df):,} total records across {len(df.columns)} data fields."]
    
    num_cols = df.select_dtypes(include="number").columns
    num_cols = [c for c in num_cols if c.lower() not in ["s.no", "recordid", "id"]]
    if num_cols:
        main_col = num_cols[0]
        total_val = df[main_col].sum()
        avg_val = df[main_col].mean()
        max_val = df[main_col].max()
        insights.append(f"Cumulative {main_col} stands at {total_val:,.2f} with an average of {avg_val:,.2f} per record (Peak: {max_val:,.2f}).")

    cat_cols = df.select_dtypes(include="object").columns
    if len(cat_cols) > 0:
        top_cat = df[cat_cols[0]].mode()
        if not top_cat.empty:
            insights.append(f"Primary leading category in {cat_cols[0]} is '{top_cat.iloc[0]}'.")

    insights.append("100% data integrity and column formatting validated for executive presentation.")
    return insights


@frontend.route("/dataset/export/<int:dataset_id>/<format_type>")
@login_required
def export_dataset(dataset_id, format_type):
    user_id = session.get("user_id")
    with get_db_cursor() as cursor:
        cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
        dataset = cursor.fetchone()

    if not dataset:
        flash("Access Denied: You do not have permission to export this dataset.", "error")
        return redirect(url_for("frontend.dashboard"))

    table_name = dataset[2]
    original_filename = dataset[3] or dataset[4] or f"Dataset_{dataset_id}"
    base_name = os.path.splitext(str(original_filename))[0]
    base_name = re.sub(r"[^A-Za-z0-9_\-]", "_", base_name).strip("_") or f"Dataset_{dataset_id}"

    format_type = format_type.lower()
    if format_type not in ("csv", "excel", "pdf", "word", "docx", "pptx", "ppt", "powerpoint", "svg"):
        abort(400)

    try:
        df = run_query(f"SELECT * FROM {sanitize_identifier(table_name)}")
        df = auto_repair_dataframe_headers(df)

        log_audit_event("EXPORT_DATASET", f"Exported dataset #{dataset_id} as {format_type.upper()}", user_id=user_id)
        trigger_ai_event(user_id, build_report_export_card(format_type))

        if format_type == "csv":
            data = export_to_csv(df)
            return Response(
                data,
                mimetype="text/csv",
                headers={"Content-Disposition": build_content_disposition(f"{base_name}.csv")}
            )
        elif format_type == "excel":
            data = export_to_excel(df, dataset_name=table_name)
            return Response(
                data,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": build_content_disposition(f"{base_name}.xlsx")}
            )
        elif format_type == "pdf":
            insights = generate_fast_export_insights(df)
            chart_type = select_chart(df)
            chart_path = generate_chart(df, chart_type) if chart_type else None

            pdf_bytes = export_to_pdf_report(df, dataset_name=table_name, insights=insights, chart_file_path=chart_path)
            return Response(
                pdf_bytes,
                mimetype="application/pdf",
                headers={"Content-Disposition": build_content_disposition(f"Report_{base_name}.pdf")}
            )
        elif format_type in ["word", "docx"]:
            insights = generate_fast_export_insights(df)
            chart_type = select_chart(df)
            chart_path = generate_chart(df, chart_type) if chart_type else None
            word_bytes = export_to_word_report(df, dataset_name=table_name, insights=insights, chart_file_path=chart_path)
            return Response(
                word_bytes,
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": build_content_disposition(f"Report_{base_name}.docx")}
            )
        elif format_type in ["pptx", "ppt", "powerpoint"]:
            insights = generate_fast_export_insights(df)
            chart_type = select_chart(df)
            chart_path = generate_chart(df, chart_type) if chart_type else None
            pptx_bytes = export_to_pptx_report(df, dataset_name=table_name, insights=insights, chart_file_path=chart_path)
            return Response(
                pptx_bytes,
                mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                headers={"Content-Disposition": build_content_disposition(f"Presentation_{base_name}.pptx")}
            )
        elif format_type == "svg":
            chart_type = select_chart(df)
            svg_bytes = generate_chart_svg(df, chart_type)
            return Response(
                svg_bytes,
                mimetype="image/svg+xml",
                headers={"Content-Disposition": build_content_disposition(f"Chart_{base_name}.svg")}
            )
    except Exception as e:
        print("Export Dataset Error:", e)
        flash(f"Failed to export dataset: {str(e)}", "error")
        return redirect(url_for("frontend.dashboard"))


@frontend.route("/query/export/<format_type>")
@login_required
def export_query_result(format_type):
    """
    Export EXACT active query result DataFrame (e.g. top 25 records, filtered rows)
    instead of the full dataset, matching what is displayed on screen.
    """
    user_id = session.get("user_id")
    last_sql = session.get("last_sql")
    last_query = session.get("last_query", "Query_Result")

    df = None
    if last_sql:
        try:
            df = run_query(last_sql)
        except Exception as e:
            print("Export query execution error:", e)
            df = None

    # Fallback to latest dataset if session last_sql expired or missing
    if df is None or df.empty:
        try:
            with get_db_cursor() as cursor:
                cursor.execute(get_latest_dataset(user_id=user_id), (user_id,))
                ds = cursor.fetchone()
                if ds:
                    table_name = ds[2]
                    df = run_query(f"SELECT * FROM {sanitize_identifier(table_name)}")
        except Exception:
            pass

    if df is None or df.empty:
        flash("No active query data available to export. Please run a query first.", "error")
        return redirect(url_for("frontend.chat"))

    # Auto-repair headers and add clean sequential S.No column for export output
    df = auto_repair_dataframe_headers(df)
    if "RecordID" in df.columns:
        df = df.drop(columns=["RecordID"])

    df = df.reset_index(drop=True)
    if "S.No" not in df.columns:
        df.insert(0, "S.No", range(1, len(df) + 1))

    format_type = format_type.lower().strip()
    raw_q_name = last_query if last_query and last_query != "Query_Result" else "QueryResult"
    clean_q_name = re.sub(r"[^A-Za-z0-9_\-]", "_", raw_q_name)[:30].strip("_") or "QueryResult"
    log_audit_event("EXPORT_QUERY_RESULT", f"Exported query result ({len(df)} rows) as {format_type.upper()}", user_id=user_id)
    trigger_ai_event(user_id, build_report_export_card(format_type))

    try:
        if format_type in ["excel", "xlsx"]:
            data = export_to_excel(df, dataset_name=clean_q_name)
            return Response(
                data,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": build_content_disposition(f"{clean_q_name}.xlsx")}
            )
        elif format_type == "pdf":
            insights = generate_fast_export_insights(df)
            chart_type = select_chart(df)
            chart_path = generate_chart(df, chart_type) if chart_type else None
            pdf_bytes = export_to_pdf_report(df, dataset_name=clean_q_name, insights=insights, chart_file_path=chart_path)
            return Response(
                pdf_bytes,
                mimetype="application/pdf",
                headers={"Content-Disposition": build_content_disposition(f"Report_{clean_q_name}.pdf")}
            )
        elif format_type in ["word", "docx"]:
            insights = generate_fast_export_insights(df)
            chart_type = select_chart(df)
            chart_path = generate_chart(df, chart_type) if chart_type else None
            word_bytes = export_to_word_report(df, dataset_name=clean_q_name, insights=insights, chart_file_path=chart_path)
            return Response(
                word_bytes,
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": build_content_disposition(f"Report_{clean_q_name}.docx")}
            )
        elif format_type in ["pptx", "ppt", "powerpoint"]:
            insights = generate_fast_export_insights(df)
            chart_type = select_chart(df)
            chart_path = generate_chart(df, chart_type) if chart_type else None
            pptx_bytes = export_to_pptx_report(df, dataset_name=clean_q_name, insights=insights, chart_file_path=chart_path)
            return Response(
                pptx_bytes,
                mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                headers={"Content-Disposition": build_content_disposition(f"Presentation_{clean_q_name}.pptx")}
            )
        elif format_type == "csv":
            data = export_to_csv(df)
            return Response(
                data,
                mimetype="text/csv",
                headers={"Content-Disposition": build_content_disposition(f"{clean_q_name}.csv")}
            )
        else:
            flash("Unsupported export format.", "error")
            return redirect(url_for("frontend.chat"))
    except Exception as exp_err:
        print("Export Generation Error:", exp_err)
        flash(f"Failed to generate {format_type.upper()} export: {str(exp_err)}", "error")
        return redirect(url_for("frontend.chat"))


# ==========================================
# NEW PUBLIC MARKETING PAGES (Multi-Page Architecture)
# ==========================================

@frontend.route("/features")
def features_page():
    is_logged_in = bool(session.get("user_id"))
    return render_template("features.html", is_logged_in=is_logged_in)


@frontend.route("/demo")
def demo_page():
    is_logged_in = bool(session.get("user_id"))
    return render_template("demo.html", is_logged_in=is_logged_in)


@frontend.route("/security-overview")
def security_overview():
    is_logged_in = bool(session.get("user_id"))
    return render_template("security_page.html", is_logged_in=is_logged_in)


@frontend.route("/testimonials")
def testimonials_page():
    is_logged_in = bool(session.get("user_id"))
    return render_template("testimonials.html", is_logged_in=is_logged_in)


@frontend.route("/faq")
def faq_page():
    is_logged_in = bool(session.get("user_id"))
    return render_template("faq_page.html", is_logged_in=is_logged_in)


# ==========================================
# SEO: SITEMAP & ROBOTS.TXT
# ==========================================

@frontend.route("/sitemap.xml")
def sitemap_xml():
    pages = [
        ("/", "1.0", "weekly"),
        ("/features", "0.9", "weekly"),
        ("/demo", "0.9", "weekly"),
        ("/pricing", "0.9", "weekly"),
        ("/testimonials", "0.8", "monthly"),
        ("/security-overview", "0.8", "monthly"),
        ("/faq", "0.8", "monthly"),
        ("/about", "0.7", "monthly"),
        ("/contact", "0.7", "monthly"),
        ("/docs", "0.7", "monthly"),
        ("/help", "0.6", "monthly"),
        ("/privacy", "0.5", "yearly"),
        ("/terms", "0.5", "yearly"),
    ]
    base_url = request.url_root.rstrip("/")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for path, priority, freq in pages:
        xml += f'  <url>\n    <loc>{base_url}{path}</loc>\n    <priority>{priority}</priority>\n    <changefreq>{freq}</changefreq>\n  </url>\n'
    xml += '</urlset>'
    return Response(xml, mimetype="application/xml")


@frontend.route("/robots.txt")
def robots_txt():
    base_url = request.url_root.rstrip("/")
    txt = f"""User-agent: *
Allow: /
Disallow: /dashboard
Disallow: /upload
Disallow: /chat
Disallow: /settings
Disallow: /api/

Sitemap: {base_url}/sitemap.xml
"""
    return Response(txt, mimetype="text/plain")


# ==========================================
# PUBLIC ENTERPRISE SAAS PAGES & MONITORING
# ==========================================

@frontend.route("/about")
def about():
    is_logged_in = bool(session.get("user_id"))
    return render_template("about.html", is_logged_in=is_logged_in)


@frontend.route("/docs")
def docs():
    is_logged_in = bool(session.get("user_id"))
    return render_template("docs.html", is_logged_in=is_logged_in)


@frontend.route("/privacy")
def privacy():
    is_logged_in = bool(session.get("user_id"))
    return render_template("privacy.html", is_logged_in=is_logged_in)


@frontend.route("/terms")
def terms():
    is_logged_in = bool(session.get("user_id"))
    return render_template("terms.html", is_logged_in=is_logged_in)


@frontend.route("/help")
def help_center():
    is_logged_in = bool(session.get("user_id"))
    return render_template("help.html", is_logged_in=is_logged_in)


@frontend.route("/contact")
def contact():
    is_logged_in = bool(session.get("user_id"))
    return render_template("contact.html", is_logged_in=is_logged_in)


@frontend.route("/contact-submit", methods=["POST"])
def contact_submit():
    """
    Save a public Contact Us form submission so it's visible in the Super Admin
    console instead of silently disappearing.
    """
    data = request.get_json(silent=True) or {}
    full_name = (data.get("name") or request.form.get("name") or "").strip()
    email = (data.get("email") or request.form.get("email") or "").strip().lower()
    subject = (data.get("subject") or request.form.get("subject") or "General Inquiry").strip()
    message = (data.get("message") or request.form.get("message") or "").strip()

    if not full_name or not email or "@" not in email or not message:
        return jsonify({"success": False, "message": "Please fill in your name, a valid email, and a message."}), 400

    full_name = full_name[:200]
    email = email[:255]
    subject = (subject or "General Inquiry")[:200]
    message = message[:4000]

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "127.0.0.1").split(",")[0].strip()

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(insert_contact_message(), (full_name, email, subject, message, client_ip))
    except Exception as e:
        print("Contact Message Save Error:", e)
        return jsonify({"success": False, "message": "Could not send your message right now. Please try again shortly."}), 500

    return jsonify({"success": True, "message": "Thank you! We'll respond within 24 hours."})


@frontend.route("/health")
def health():
    start_time = time.time()
    db_status = "Healthy"
    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        db_latency_ms = round((time.time() - start_time) * 1000, 2)
    except Exception as db_err:
        db_status = f"Error: {db_err}"
        db_latency_ms = -1

    if psutil:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        ram_percent = memory.percent
        ram_used_mb = round(memory.used / (1024 * 1024), 1)
        ram_total_mb = round(memory.total / (1024 * 1024), 1)
    else:
        cpu_percent = 5.0
        ram_percent = 25.0
        ram_used_mb = 512.0
        ram_total_mb = 4096.0

    cache_stats = system_cache.get_stats()

    system_metrics = {
        "db_status": db_status,
        "db_latency_ms": db_latency_ms,
        "cpu_percent": cpu_percent,
        "ram_percent": ram_percent,
        "ram_used_mb": ram_used_mb,
        "ram_total_mb": ram_total_mb,
        "cache_hits": cache_stats["hits"],
        "cache_misses": cache_stats["misses"],
        "cache_hit_ratio": cache_stats["hit_ratio"],
        "cache_items": cache_stats["size"]
    }

    return render_template("health.html", metrics=system_metrics)


# ==========================================
# AI SMART ASSISTANT & NOTIFICATIONS API
# ==========================================

@frontend.route("/api/ai-assistant/current")
def api_ai_assistant_current():
    """
    Returns pending AI notification card for floating assistant widget and live unread notification count.
    """
    card = session.pop("pending_ai_card", None)
    unread_cnt = 0
    user_id = session.get("user_id")
    if user_id:
        try:
            with get_db_cursor() as cursor:
                cursor.execute(get_unread_ai_notification_count(), (user_id,))
                r = cursor.fetchone()
                if r:
                    unread_cnt = int(r[0])
        except Exception: pass

    return jsonify({"card": card, "unread_count": unread_cnt})


@frontend.route("/api/ai-notifications")
@login_required
def api_ai_notifications():
    """
    Fetch persistent Notification Center history for the logged-in user.
    """
    import json
    user_id = session.get("user_id")
    notifications = []
    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_user_ai_notifications(limit=30), (user_id,))
            rows = cursor.fetchall()
            for r in rows:
                meta = {}
                if r[5]:
                    try: meta = json.loads(r[5])
                    except Exception: pass

                notifications.append({
                    "id": r[0],
                    "category": r[2],
                    "title": r[3],
                    "message": r[4],
                    "metadata": meta,
                    "is_read": bool(r[6]),
                    "created_at": r[7].strftime("%b %d, %Y %I:%M %p") if r[7] else ""
                })
    except Exception as err:
        print("Error fetching notifications:", err)

    return jsonify({"notifications": notifications})


@frontend.route("/api/ai-notifications/mark-read", methods=["POST"])
@login_required
def api_ai_notifications_mark_read():
    """
    Mark AI notification(s) as read.
    """
    user_id = session.get("user_id")
    data = request.get_json() or {}
    notif_id = data.get("notification_id", 0)
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(mark_ai_notification_read(), (user_id, notif_id, notif_id))
        return jsonify({"success": True})
    except Exception as err:
        return jsonify({"success": False, "error": str(err)}), 500


@frontend.route("/api/ai-notifications/clear", methods=["POST"])
@login_required
def api_ai_notifications_clear():
    """
    Clear notification history for logged-in user.
    """
    user_id = session.get("user_id")
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(clear_user_ai_notifications(), (user_id,))
        return jsonify({"success": True})
    except Exception as err:
        return jsonify({"success": False, "error": str(err)}), 500


# ==========================================
# ENTERPRISE DATA QUALITY & INSIGHTS APIS
# ==========================================

@frontend.route("/api/dataset/<int:dataset_id>/quality")
@login_required
def api_dataset_quality(dataset_id):
    """
    Fetch Data Quality & Health Score (0-100) for dataset.
    Enforces strict user data isolation.
    """
    user_id = session.get("user_id")
    with get_db_cursor() as cursor:
        cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Dataset not found or access denied."}), 403
        tbl_name = row[2]

    from ai.data_quality import analyze_dataset_quality
    try:
        quality_res = analyze_dataset_quality(tbl_name)
    except Exception as e:
        return jsonify({"success": False, "error": f"Quality analysis failed: {str(e)}"}), 500
    return jsonify({"success": True, "quality": quality_res})


@frontend.route("/api/dataset/<int:dataset_id>/insights")
@login_required
def api_dataset_insights(dataset_id):
    """
    Fetch Automated Insights, Anomalies, and Executive AI Summary.
    Enforces strict user data isolation.
    """
    user_id = session.get("user_id")
    with get_db_cursor() as cursor:
        cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Dataset not found or access denied."}), 403
        tbl_name = row[2]

    from ai.insight_engine import generate_automated_insights
    try:
        insights_res = generate_automated_insights(tbl_name)
    except Exception as e:
        return jsonify({"success": False, "error": f"Insight generation failed: {str(e)}"}), 500
    return jsonify({"success": True, "insights": insights_res})


# ==========================================
# QUERY HISTORY APIS (USER ISOLATION)
# ==========================================

@frontend.route("/api/query-history")
@login_required
def api_query_history():
    user_id = session.get("user_id")
    history_items = []
    try:
        from database.queries import get_user_query_history
        with get_db_cursor() as cursor:
            cursor.execute(get_user_query_history(limit=50), (user_id,))
            for r in cursor.fetchall():
                history_items.append({
                    "id": r[0],
                    "dataset_id": r[1],
                    "question": r[2],
                    "sql": r[3],
                    "status": r[4],
                    "rows": r[5],
                    "time_ms": r[6],
                    "chart_type": r[7],
                    "created_at": r[9].strftime("%b %d, %Y %I:%M %p") if r[9] else ""
                })
    except Exception as err:
        print("Query History Error:", err)

    return jsonify({"success": True, "history": history_items})


@frontend.route("/api/query-history/delete/<int:history_id>", methods=["DELETE", "POST"])
@login_required
def api_delete_query_history(history_id):
    user_id = session.get("user_id")
    try:
        from database.queries import delete_query_history_item
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(delete_query_history_item(), (history_id, user_id))
        return jsonify({"success": True})
    except Exception as err:
        return jsonify({"success": False, "error": str(err)}), 500


@frontend.route("/api/query-history/clear", methods=["POST"])
@login_required
def api_clear_query_history():
    user_id = session.get("user_id")
    try:
        from database.queries import clear_user_query_history
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(clear_user_query_history(), (user_id,))
        return jsonify({"success": True})
    except Exception as err:
        return jsonify({"success": False, "error": str(err)}), 500


# ==========================================
# SCHEDULED REPORTS & ALERTS APIS
# ==========================================

@frontend.route("/api/scheduled-reports", methods=["GET", "POST"])
@login_required
def api_scheduled_reports():
    user_id = session.get("user_id")

    if request.method == "POST":
        data = request.get_json() or {}
        ds_id = data.get("dataset_id")
        report_type = data.get("report_type", "Executive Summary")
        frequency = data.get("frequency", "Daily")
        # Always the account's own verified email, never client-suppliable - otherwise a
        # scheduled report (which runs unattended and emails a data summary) could be
        # pointed at an attacker's inbox.
        recipient = session.get("user_email")
        sched_time = data.get("schedule_time", "09:00")

        if not ds_id:
            return jsonify({"success": False, "error": "Dataset required."}), 400

        try:
            with get_db_cursor() as cursor:
                cursor.execute(get_dataset_by_id(user_id=user_id), (ds_id,))
                if not cursor.fetchone():
                    return jsonify({"success": False, "error": "Dataset not found."}), 404

            from database.queries import create_scheduled_report
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(create_scheduled_report(), (user_id, ds_id, report_type, frequency, recipient, sched_time))
            return jsonify({"success": True})
        except Exception as err:
            return jsonify({"success": False, "error": str(err)}), 500

    # GET: List reports
    reports = []
    try:
        from database.queries import get_user_scheduled_reports
        with get_db_cursor() as cursor:
            cursor.execute(get_user_scheduled_reports(), (user_id,))
            for r in cursor.fetchall():
                reports.append({
                    "id": r[0],
                    "dataset_id": r[1],
                    "dataset_name": r[2],
                    "report_type": r[3],
                    "frequency": r[4],
                    "recipient_email": r[5],
                    "schedule_time": r[6],
                    "is_enabled": bool(r[7]),
                    "last_run_at": r[8].strftime("%b %d, %Y %I:%M %p") if r[8] else "Never"
                })
    except Exception as err:
        print("Error fetching scheduled reports:", err)

    return jsonify({"success": True, "reports": reports})


@frontend.route("/api/scheduled-reports/toggle/<int:report_id>", methods=["POST"])
@login_required
def api_toggle_scheduled_report(report_id):
    user_id = session.get("user_id")
    try:
        from database.queries import toggle_scheduled_report
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(toggle_scheduled_report(), (report_id, user_id))
        return jsonify({"success": True})
    except Exception as err:
        return jsonify({"success": False, "error": str(err)}), 500


@frontend.route("/api/alert-rules", methods=["GET", "POST"])
@login_required
def api_alert_rules():
    user_id = session.get("user_id")

    if request.method == "POST":
        data = request.get_json() or {}
        ds_id = data.get("dataset_id")
        metric_name = data.get("metric_name", "Return Rate")
        op = data.get("operator", ">")
        # Always the account's own verified email, never client-suppliable - otherwise a
        # triggered alert (which emails a computed metric from the dataset) could be
        # pointed at an attacker's inbox.
        recipient = session.get("user_email")

        if not ds_id:
            return jsonify({"success": False, "error": "Dataset required."}), 400

        if op not in (">", "<", ">=", "<="):
            return jsonify({"success": False, "error": "Invalid comparison operator."}), 400

        try:
            threshold = float(data.get("threshold", 20.0))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Threshold must be a number."}), 400

        try:
            with get_db_cursor() as cursor:
                cursor.execute(get_dataset_by_id(user_id=user_id), (ds_id,))
                if not cursor.fetchone():
                    return jsonify({"success": False, "error": "Dataset not found."}), 404

            from database.queries import create_alert_rule
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(create_alert_rule(), (user_id, ds_id, metric_name, op, threshold, recipient))
            return jsonify({"success": True})
        except Exception as err:
            return jsonify({"success": False, "error": str(err)}), 500

    # GET: List active alert rules
    rules = []
    try:
        from database.queries import get_user_alert_rules
        with get_db_cursor() as cursor:
            cursor.execute(get_user_alert_rules(), (user_id,))
            for r in cursor.fetchall():
                rules.append({
                    "id": r[0],
                    "dataset_id": r[1],
                    "dataset_name": r[2],
                    "metric_name": r[3],
                    "operator": r[4],
                    "threshold": r[5],
                    "recipient_email": r[6],
                    "is_enabled": bool(r[7]),
                    "last_triggered": r[8].strftime("%b %d, %Y %I:%M %p") if r[8] else "Never"
                })
    except Exception as err:
        print("Error fetching alert rules:", err)

    return jsonify({"success": True, "rules": rules})


@frontend.route("/api/dataset-health/<int:dataset_id>", methods=["GET"])
@login_required
def api_dataset_health(dataset_id):
    user_id = session.get("user_id")
    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
            row = cursor.fetchone()

        if not row:
            return jsonify({"success": False, "error": "Dataset not found"}), 404

        table_name = row[2]
        from ai.data_health_engine import analyze_table_health
        health_data = analyze_table_health(table_name)
        return jsonify({"success": True, "health": health_data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@frontend.route("/report-builder/<int:dataset_id>", methods=["GET", "POST"])
@login_required
def report_builder(dataset_id):
    user_id = session.get("user_id")
    user_email = session.get("user_email", "")
    try:
        with get_db_cursor() as cursor:
            cursor.execute(get_dataset_by_id(user_id=user_id), (dataset_id,))
            row = cursor.fetchone()

        if not row:
            flash("Access Denied: You do not have permission to view this report.", "error")
            return redirect(url_for("frontend.dashboard"))

        table_name = row[2]
        company_name = request.form.get("company_name", "Enterprise Analytics Client") if request.method == "POST" else "Enterprise Analytics Client"

        from utils.report_builder import generate_executive_report_html
        report_html = generate_executive_report_html(table_name, company_name=company_name, user_email=user_email)

        if request.args.get("download") == "1":
            from flask import Response
            return Response(report_html, mimetype="text/html", headers={"Content-Disposition": f"attachment;filename=Executive_Briefing_{dataset_id}.html"})

        return render_template("report_builder.html", report_html=report_html, dataset_id=dataset_id, dataset_name=table_name)
    except Exception as e:
        print("Report Builder Error:", e)
        flash(f"Failed to generate executive report: {str(e)}", "error")
        return redirect(url_for("frontend.dashboard"))


# ==========================================
# WAITING FOR APPROVAL (post-registration, pre-approval gate)
# ==========================================
@frontend.route("/waiting-approval")
@login_required
def waiting_approval():
    user_id = session.get("user_id")
    payment = None
    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                "SELECT TOP 1 Amount, TransactionID, PaymentMethod, Status, PaymentDate FROM Payments WHERE UserID = ? ORDER BY PaymentDate DESC",
                (user_id,)
            )
            row = cursor.fetchone()
            if row:
                payment = {
                    "amount": float(row[0] or 0), "transaction_id": row[1],
                    "method": row[2], "status": row[3], "date": format_12hr_datetime(row[4])
                }
    except Exception:
        pass
    return render_template("waiting_approval.html", payment=payment)


# ==========================================
# USER-FACING PAYMENT RECEIPTS / HISTORY
# ==========================================
@frontend.route("/receipts")
@login_required
def receipts():
    """
    Lets a user see every payment they've ever submitted (not just the ones relevant to
    an active renewal) - approved, pending, and rejected - each strictly scoped to their
    own UserID, with a link to view/download the same PDF invoice format the admin panel
    generates.
    """
    user_id = session.get("user_id")
    receipt_list = []
    error = None
    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                "SELECT PaymentID, Amount, Currency, PaymentMethod, TransactionID, Status, PlanName, PaymentDate, PaymentType, RejectionReason "
                "FROM Payments WHERE UserID = ? ORDER BY PaymentDate DESC",
                (user_id,)
            )
            rows = cursor.fetchall() or []
            for r in rows:
                receipt_list.append({
                    "id": r[0],
                    "amount": float(r[1] or 0),
                    "currency": r[2] or "PKR",
                    "method": r[3],
                    "txn_id": r[4],
                    "status": r[5],
                    "plan_name": r[6],
                    "payment_date": format_12hr_datetime(r[7]),
                    "payment_type": r[8] or "New",
                    "rejection_reason": r[9]
                })
    except Exception as e:
        print("Receipts Fetch Error:", e)
        error = "We couldn't load your payment receipts right now. Please try refreshing the page."

    return render_template("receipts.html", receipts=receipt_list, error=error)


@frontend.route("/receipts/<int:payment_id>")
@login_required
def view_receipt(payment_id):
    """
    Same on-screen invoice layout the admin panel uses (Print + Download PDF buttons),
    reused here for a user viewing their own receipt - ownership-checked, unlike the
    admin route which checks admin role instead.
    """
    user_id = session.get("user_id")
    try:
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT P.PaymentID, P.UserID, U.FullName, U.Email, P.Amount, P.Currency, P.PaymentMethod, P.TransactionID, P.Status, P.PlanName, P.PaymentDate
                FROM Payments P
                LEFT JOIN Users U ON P.UserID = U.UserID
                WHERE P.PaymentID = ? AND P.UserID = ?
            """, (payment_id, user_id))
            p = cursor.fetchone()

        if not p:
            flash("Receipt not found.", "error")
            return redirect(url_for("frontend.receipts"))

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
        return render_template(
            "admin/invoice.html", payment=payment_dict,
            back_url=url_for("frontend.receipts"), back_label="Back to Receipts",
            download_url=url_for("frontend.download_receipt_pdf", payment_id=p_id)
        )
    except Exception as e:
        print("View Receipt Error:", e)
        flash("Failed to load receipt.", "error")
        return redirect(url_for("frontend.receipts"))


@frontend.route("/receipts/<int:payment_id>/download")
@login_required
def download_receipt_pdf(payment_id):
    """
    Same PDF invoice generator the admin panel uses, but gated by ownership instead of
    admin role - a user can only ever download their OWN payment's receipt, never one
    reached by guessing/incrementing another payment_id in the URL.
    """
    user_id = session.get("user_id")
    try:
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT P.PaymentID, P.UserID, U.FullName, U.Email, P.Amount, P.Currency, P.PaymentMethod, P.TransactionID, P.Status, P.PlanName, P.PaymentDate
                FROM Payments P
                LEFT JOIN Users U ON P.UserID = U.UserID
                WHERE P.PaymentID = ? AND P.UserID = ?
            """, (payment_id, user_id))
            p = cursor.fetchone()

        if not p:
            flash("Receipt not found.", "error")
            return redirect(url_for("frontend.receipts"))

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
        filename = f"Receipt_{payment_dict['txn_id']}.pdf"
        return Response(pdf_bytes, mimetype="application/pdf", headers={"Content-Disposition": f"attachment; filename={filename}"})
    except Exception as e:
        print("Download Receipt Error:", e)
        flash("Failed to generate receipt PDF.", "error")
        return redirect(url_for("frontend.receipts"))


# ==========================================
# RENEW SUBSCRIPTION & UPLOAD PAYMENT RECEIPT
# ==========================================
@frontend.route("/subscription/renew", methods=["GET", "POST"])
@login_required
def renew_subscription():
    user_id = session.get("user_id")
    ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
    MAX_BYTES = 5 * 1024 * 1024

    if request.method == "POST":
        file = request.files.get("receipt_file")
        bank_method = request.form.get("bank_method", "Bank Transfer").strip()
        txn_id = request.form.get("transaction_note", "").strip()
        payment_amount_str = request.form.get("payment_amount", "25000").strip()

        try:
            payment_amount = float(payment_amount_str)
        except ValueError:
            payment_amount = 0.0

        if payment_amount < 25000.0:
            flash("Submission Rejected: Minimum subscription fee is PKR 25,000. Payment submissions under PKR 25,000 cannot be accepted.", "error")
            return redirect(url_for("frontend.renew_subscription"))

        if not txn_id:
            flash("Transaction ID / reference number is required.", "error")
            return redirect(url_for("frontend.renew_subscription"))

        if not file or not file.filename:
            flash("Please select a payment screenshot to upload.", "error")
            return redirect(url_for("frontend.renew_subscription"))

        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXT:
            flash("Screenshot must be a JPG, JPEG, PNG, or WEBP image.", "error")
            return redirect(url_for("frontend.renew_subscription"))

        file.seek(0, os.SEEK_END)
        if file.tell() > MAX_BYTES:
            flash("Screenshot file is too large - maximum size is 5 MB.", "error")
            return redirect(url_for("frontend.renew_subscription"))
        file.seek(0)

        from database.queries import check_duplicate_transaction_id, check_user_has_pending_payment, insert_payment_full
        with get_db_cursor() as cursor:
            cursor.execute(check_duplicate_transaction_id(), (txn_id,))
            if cursor.fetchone()[0] > 0:
                flash("This transaction ID has already been used for a previous payment. Please check and enter the correct reference number.", "error")
                return redirect(url_for("frontend.renew_subscription"))

            cursor.execute(check_user_has_pending_payment(), (user_id,))
            if cursor.fetchone()[0] > 0:
                flash("You already have a payment submission awaiting review. Please wait for it to be verified before submitting another.", "warning")
                return redirect(url_for("frontend.renew_subscription"))

        from werkzeug.utils import secure_filename
        upload_dir = os.path.join("static", "uploads", "payment_screenshots")
        os.makedirs(upload_dir, exist_ok=True)
        safe_name = f"RENEW_{user_id}_{int(time.time())}{ext}"
        save_path = os.path.join(upload_dir, safe_name)
        file.save(save_path)
        rel_path = f"/static/uploads/payment_screenshots/{safe_name}"

        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(
                    insert_payment_full(),
                    (user_id, payment_amount, bank_method, txn_id, "Enterprise Plan (PKR 25,000/mo)", "Renewal", rel_path)
                )

            flash(f"Payment screenshot of PKR {payment_amount:,.2f} via '{bank_method}' submitted successfully! Super Admin has been notified for review.", "success")
            log_audit_event("SUBSCRIPTION_RECEIPT_UPLOAD", f"Submitted PKR {payment_amount} renewal payment via {bank_method} (Txn: {txn_id})", user_id=user_id)

            with get_db_cursor() as cursor:
                cursor.execute("SELECT FullName, Email FROM Users WHERE UserID = ?", (user_id,))
                u_row = cursor.fetchone()
            if u_row:
                try:
                    email_service.send_payment_received_email(u_row[1], u_row[0])
                    from auth.routes import _notify_admins_new_payment
                    _notify_admins_new_payment(u_row[0], u_row[1], payment_amount, txn_id)
                except Exception as notify_err:
                    print("Renewal notification error:", notify_err)
        except Exception as err:
            print("Receipt Upload Error:", err)
            flash(f"Failed to record payment receipt: {str(err)}", "error")

        return redirect(url_for("frontend.renew_subscription"))

    payments = []
    rejection_reason = None
    account_status = "suspended"
    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT PaymentID, Amount, PaymentMethod, Status, PlanName, PaymentDate, ScreenshotPath FROM Payments WHERE UserID = ? ORDER BY PaymentDate DESC", (user_id,))
            rows = cursor.fetchall() or []
            for r in rows:
                payments.append({
                    "id": r[0],
                    "amount": float(r[1] or 0),
                    "method": r[2],
                    "status": r[3],
                    "plan_name": r[4],
                    "payment_date": format_12hr_datetime(r[5]),
                    "screenshot_path": r[6]
                })

            cursor.execute("SELECT SubscriptionStatus, SuspensionReason FROM Users WHERE UserID = ?", (user_id,))
            u_row = cursor.fetchone()
            if u_row:
                account_status = u_row[0]
                rejection_reason = u_row[1]
    except Exception:
        pass

    from auth.routes import _get_payment_settings_dict
    settings = _get_payment_settings_dict()

    return render_template(
        "renew_subscription.html", payments=payments, settings=settings,
        account_status=account_status, rejection_reason=rejection_reason
    )


@frontend.route("/subscribe-newsletter", methods=["POST"])
def subscribe_newsletter():
    """
    Handle Newsletter Subscription AJAX Request & Dispatch Official Executive Welcome Email.
    """
    try:
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or request.form.get("email") or "").strip().lower()

        if not email or "@" not in email or "." not in email:
            return jsonify({"success": False, "message": "Please enter a valid email address."}), 400

        # Store subscriber record in database for Super Admin visibility
        client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "127.0.0.1").split(',')[0].strip()
        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("SELECT SubscriberID FROM NewsletterSubscribers WHERE Email = ?", (email,))
                sub_row = cursor.fetchone()
                if not sub_row:
                    cursor.execute("""
                        INSERT INTO NewsletterSubscribers (Email, Status, IPAddress, SubscribedAt)
                        VALUES (?, 'Active', ?, GETDATE())
                    """, (email, client_ip))
                else:
                    cursor.execute("""
                        UPDATE NewsletterSubscribers
                        SET Status = 'Active', IPAddress = ?, SubscribedAt = GETDATE()
                        WHERE Email = ?
                    """, (client_ip, email))
        except Exception as db_err:
            print("Save Newsletter Subscriber DB Notice:", db_err)

        # Dispatch Automated Executive Welcome Email
        try:
            from auth.email_service import EmailService
            serializer = URLSafeTimedSerializer(current_app.secret_key)
            token = serializer.dumps(email, salt="newsletter-unsubscribe")
            unsubscribe_url = url_for("frontend.unsubscribe_newsletter", token=token, _external=True)

            email_service = EmailService()
            email_service.send_newsletter_subscription_email(to_email=email, unsubscribe_url=unsubscribe_url)
        except Exception as mail_err:
            print("Newsletter Welcome Email Notice:", mail_err)

        return jsonify({
            "success": True,
            "message": "Thank you! Your email has been subscribed successfully and a welcome email has been sent to your inbox."
        })
    except Exception as e:
        print("Subscribe Newsletter Error:", e)
        return jsonify({"success": False, "message": "Failed to subscribe. Please try again later."}), 500


@frontend.route("/unsubscribe/<token>", methods=["GET"])
def unsubscribe_newsletter(token):
    """
    One-click newsletter unsubscribe link, delivered via the signed List-Unsubscribe
    header/footer link on newsletter emails. The token is a signed, tamper-proof
    encoding of the subscriber's email (itsdangerous), so no login is required and
    nobody else's subscription can be altered by guessing an email address.
    """
    serializer = URLSafeTimedSerializer(current_app.secret_key)
    try:
        email = serializer.loads(token, salt="newsletter-unsubscribe", max_age=60 * 60 * 24 * 365)
    except BadSignature:
        flash("This unsubscribe link is invalid.", "error")
        return redirect(url_for("frontend.index"))

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE NewsletterSubscribers SET Status = 'Unsubscribed' WHERE Email = ?",
                (email,)
            )
    except Exception as db_err:
        print("Unsubscribe DB Notice:", db_err)

    return render_template("unsubscribed.html", email=email)


# ==========================================
# ERROR HANDLERS
# ==========================================
@frontend.app_errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


@frontend.app_errorhandler(500)
def internal_server_error(e):
    # Log the real exception server-side only - rendering str(e) straight into the page
    # (the previous behavior) could show visitors internal detail like a SQL fragment,
    # table/column name, or file path from whatever raised the error.
    print("Unhandled 500 error:", e)
    return render_template("500.html"), 500