import os
import re
import math
import time
import pandas as pd
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
    session
)

try:
    import psutil
except ImportError:
    psutil = None

from database.connection import (
    get_connection,
    get_db_cursor,
    run_query,
    get_latest_table,
    get_table_columns,
    get_table_preview,
    is_safe_identifier,
    sanitize_identifier,
    init_db
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
    delete_dataset_table,
    get_dataset_table_name,
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
    clear_user_ai_notifications
)

from ai.smart_assistant import (
    trigger_ai_event,
    build_dataset_upload_card,
    build_dataset_delete_card,
    build_dataset_edit_card,
    build_ai_query_initiated_card,
    build_ai_query_completed_card,
    build_report_export_card,
    get_first_name
)

from uploads.csv_upload import upload_csv
from uploads.multi_loader import process_file_upload, SUPPORTED_EXTENSIONS
from uploads.data_loader import auto_repair_dataframe_headers
from ai.smart_selector import detect_best_dataset_for_query
from ai.multi_dataset import execute_multi_dataset_analysis
from ai.voice import clean_voice_command, format_tts_response
from utils.logger import log_ai_event, log_voice_event, log_upload_event, log_query_event, log_export_event
from ai.gemini import (
    ask_gemini,
    execute_sql_with_retry,
    generate_data_business_summary,
    UNRELATED_MESSAGE
)
from ai.sql_agent import explain_result
from ai.data_summary import format_ai_explanation
from ai.analytics import (
    generate_data_quality_report,
    clean_dataset,
    generate_ai_insights
)
from ai.prediction import generate_forecast, detect_time_and_target_columns
from ai.privacy_guard import sanitize_prompt_payload, mask_dataframe_pii
from visualization.chart_selector import select_chart
from visualization.chart_generator import generate_chart, generate_chart_svg
from utils.exporters import (
    export_to_csv,
    export_to_excel,
    export_to_pdf_report,
    export_to_word_report,
    export_to_pptx_report
)
from utils.helpers import format_bytes
from utils.cache import system_cache
from auth.decorators import (
    login_required,
    role_required,
    admin_required,
    manager_required,
    viewer_allowed
)
from auth.security import log_audit_event

frontend = Blueprint(
    "frontend",
    __name__,
    template_folder="../templates",
    static_folder="../static"
)



# Auto initialize system tables & columns
try:
    init_db()
except Exception:
    pass


# ==========================================
# HOME
# ==========================================
@frontend.route("/")
def home():
    if not session.get("user_id"):
        return redirect(url_for("auth.login"))
    return redirect(url_for("frontend.dashboard"))


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
                query_str = search_datasets(user_id=user_id, sort_by=sort_by, order=order, date_from=date_from if date_from else None, date_to=date_to if date_to else None)
            else:
                query_str = get_all_datasets(user_id=user_id, sort_by=sort_by, order=order, date_from=date_from if date_from else None, date_to=date_to if date_to else None)

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
        user_datasets = cursor.fetchall()

    if not user_datasets:
        if is_ajax:
            return jsonify({"success": False, "error": "No dataset uploaded in your account. Please upload a dataset first."})
        return render_template("chat.html", user_datasets=[], selected_dataset=None, error="No dataset uploaded in your account. Please upload a dataset first.")

    if request.is_json and request.get_json():
        json_data = request.get_json()
        question = str(json_data.get("question", "")).strip()
        dataset_id = json_data.get("dataset_id")
        if dataset_id is not None:
            try: dataset_id = int(dataset_id)
            except Exception: dataset_id = None
    else:
        question = request.values.get("question", "").strip()
        dataset_id = request.values.get("dataset_id", type=int)

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

    # Conversation Context Memory (Feature 3 & Feature 7)
    last_query = session.get("last_query", "")
    last_sql = session.get("last_sql", "")

    # Check for relative follow-up context (e.g. "Sort descending", "Create chart", "Show top 10")
    if last_sql and any(kw in question.lower() for kw in ["sort", "order", "descending", "ascending", "filter", "where"]):
        if "order by" not in last_sql.lower() and "sort" in question.lower():
            if "desc" in question.lower() or "descending" in question.lower():
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

    ai_result = execute_sql_with_retry(question_for_ai, table_name, columns, max_retries=3)
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
        if is_ajax:
            return jsonify({
                "success": False,
                "is_out_of_domain": False,
                "sql": ai_result["sql"],
                "error": f"SQL Execution Error: {ai_result['error']}",
                "tts_speech": "Sorry, I could not execute that query on the dataset."
            })
        return render_template(
            "chat.html",
            user_datasets=user_datasets,
            selected_dataset=selected_dataset,
            question=question,
            sql=ai_result["sql"],
            error=f"SQL Execution Error: {ai_result['error']}",
            tts_speech="Sorry, I could not execute that query on the dataset."
        )

    df = ai_result["df"]
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

    try:
        explanation = generate_data_business_summary(selected_dataset[2], df, question)
    except Exception:
        explanation = f"Query executed successfully on dataset [{selected_dataset[2]}]. Displaying {len(df)} matching records."

    # Natural Spoken Speech formatting (Feature 2)
    tts_speech = format_tts_response(question, df, explanation)

    chart_path = None
    try:
        chart_type = select_chart(df)
        if chart_type:
            chart_path = generate_chart(df, chart_type)
    except Exception as chart_err:
        print("Chart Error:", chart_err)

    # Auto-repair and infer clean human headers for any Unnamed/blank columns
    df = auto_repair_dataframe_headers(df)

    # Clean technical RecordID column and prepend neat 1-to-N S.No sequential column
    if "RecordID" in df.columns:
        df = df.drop(columns=["RecordID"])

    df = df.reset_index(drop=True)
    if "S.No" not in df.columns:
        df.insert(0, "S.No", range(1, len(df) + 1))

    result_html = df.to_html(
        classes="table table-bordered table-striped custom-table",
        index=False
    )

    if is_ajax:
        return jsonify({
            "success": True,
            "question": question,
            "selected_dataset_id": selected_dataset[0],
            "selected_dataset_name": selected_dataset[2],
            "is_out_of_domain": False,
            "sql": ai_result["sql"],
            "result_html": result_html,
            "explanation": format_ai_explanation(explanation),
            "tts_speech": tts_speech,
            "chart": chart_path,
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
        result=result_html,
        explanation=explanation,
        chart=chart_path,
        execution_time_ms=ai_result["execution_time_ms"],
        confidence=ai_result["confidence"],
        retries=ai_result["retries"],
        rows_returned=ai_result["rows_returned"],
        tts_speech=tts_speech
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

    dataset_tables = []
    with get_db_cursor() as cursor:
        for ds_id in dataset_ids:
            cursor.execute(get_dataset_by_id(user_id=user_id), (ds_id,))
            row = cursor.fetchone()
            if row:
                dataset_tables.append((row[0], row[2], row[3]))

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
    per_page = 100

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
        new_name = request.form.get("dataset_name", "").strip()
        tags = request.form.get("tags", "").strip()
        if new_name:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(update_dataset_name(user_id=user_id), (new_name, dataset_id))
                cursor.execute(update_dataset_tags(user_id=user_id), (tags, dataset_id))

            system_cache.invalidate_pattern(f"dash_metrics_{user_id}")
            log_audit_event("EDIT_DATASET", f"Renamed dataset #{dataset_id} to '{new_name}'", user_id=user_id)
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
    original_filename = dataset[3]
    df = run_query(f"SELECT * FROM {sanitize_identifier(table_name)}")

    format_type = format_type.lower()
    log_audit_event("EXPORT_DATASET", f"Exported dataset #{dataset_id} as {format_type.upper()}", user_id=user_id)
    trigger_ai_event(user_id, build_report_export_card(format_type))

    if format_type == "csv":
        data = export_to_csv(df)
        return Response(
            data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={os.path.splitext(original_filename)[0]}.csv"}
        )
    elif format_type == "excel":
        data = export_to_excel(df, dataset_name=table_name)
        return Response(
            data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={os.path.splitext(original_filename)[0]}.xlsx"}
        )
    elif format_type == "pdf":
        insights = generate_ai_insights(df)
        chart_type = select_chart(df)
        chart_path = generate_chart(df, chart_type) if chart_type else None
        
        pdf_bytes = export_to_pdf_report(df, dataset_name=table_name, insights=insights, chart_file_path=chart_path)
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=report_{os.path.splitext(original_filename)[0]}.pdf"}
        )
    elif format_type in ["word", "docx"]:
        insights = generate_ai_insights(df)
        chart_type = select_chart(df)
        chart_path = generate_chart(df, chart_type) if chart_type else None
        word_bytes = export_to_word_report(df, dataset_name=table_name, insights=insights, chart_file_path=chart_path)
        return Response(
            word_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename=report_{os.path.splitext(original_filename)[0]}.docx"}
        )
    elif format_type in ["pptx", "ppt", "powerpoint"]:
        insights = generate_ai_insights(df)
        chart_type = select_chart(df)
        chart_path = generate_chart(df, chart_type) if chart_type else None
        pptx_bytes = export_to_pptx_report(df, dataset_name=table_name, insights=insights, chart_file_path=chart_path)
        return Response(
            pptx_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f"attachment; filename=presentation_{os.path.splitext(original_filename)[0]}.pptx"}
        )
    elif format_type == "svg":
        chart_type = select_chart(df)
        svg_bytes = generate_chart_svg(df, chart_type)
        return Response(
            svg_bytes,
            mimetype="image/svg+xml",
            headers={"Content-Disposition": f"attachment; filename=chart_{table_name}.svg"}
        )
    else:
        abort(400)


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
                    df = run_query(f"SELECT TOP 50 * FROM {sanitize_identifier(table_name)}")
        except Exception:
            pass

    if df is None or df.empty:
        flash("No active query data available to export. Please run a query first.", "error")
        return redirect(url_for("frontend.chat_view"))

    # Auto-repair headers for clean output
    df = auto_repair_dataframe_headers(df)

    format_type = format_type.lower().strip()
    clean_q_name = re.sub(r"[^A-Za-z0-9_]", "_", last_query)[:30].strip("_") or "QueryResult"
    log_audit_event("EXPORT_QUERY_RESULT", f"Exported query result ({len(df)} rows) as {format_type.upper()}", user_id=user_id)
    trigger_ai_event(user_id, build_report_export_card(format_type))

    try:
        if format_type in ["excel", "xlsx"]:
            data = export_to_excel(df, dataset_name=clean_q_name)
            return Response(
                data,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f"attachment; filename={clean_q_name}.xlsx"}
            )
        elif format_type == "pdf":
            insights = generate_ai_insights(df)
            chart_type = select_chart(df)
            chart_path = generate_chart(df, chart_type) if chart_type else None
            pdf_bytes = export_to_pdf_report(df, dataset_name=clean_q_name, insights=insights, chart_file_path=chart_path)
            return Response(
                pdf_bytes,
                mimetype="application/pdf",
                headers={"Content-Disposition": f"attachment; filename=report_{clean_q_name}.pdf"}
            )
        elif format_type in ["word", "docx"]:
            insights = generate_ai_insights(df)
            chart_type = select_chart(df)
            chart_path = generate_chart(df, chart_type) if chart_type else None
            word_bytes = export_to_word_report(df, dataset_name=clean_q_name, insights=insights, chart_file_path=chart_path)
            return Response(
                word_bytes,
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f"attachment; filename=report_{clean_q_name}.docx"}
            )
        elif format_type in ["pptx", "ppt", "powerpoint"]:
            insights = generate_ai_insights(df)
            chart_type = select_chart(df)
            chart_path = generate_chart(df, chart_type) if chart_type else None
            pptx_bytes = export_to_pptx_report(df, dataset_name=clean_q_name, insights=insights, chart_file_path=chart_path)
            return Response(
                pptx_bytes,
                mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                headers={"Content-Disposition": f"attachment; filename=presentation_{clean_q_name}.pptx"}
            )
        elif format_type == "csv":
            data = export_to_csv(df)
            return Response(
                data,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={clean_q_name}.csv"}
            )
        else:
            flash("Unsupported export format.", "error")
            return redirect(url_for("frontend.chat_view"))
    except Exception as exp_err:
        print("Export Generation Error:", exp_err)
        flash(f"Failed to generate {format_type.upper()} export: {str(exp_err)}", "error")
        return redirect(url_for("frontend.chat_view"))


# ==========================================
# PUBLIC ENTERPRISE SAAS PAGES & MONITORING
# ==========================================

@frontend.route("/about")
def about():
    return render_template("about.html")


@frontend.route("/docs")
def docs():
    return render_template("docs.html")


@frontend.route("/privacy")
def privacy():
    return render_template("privacy.html")


@frontend.route("/terms")
def terms():
    return render_template("terms.html")


@frontend.route("/help")
def help_center():
    return render_template("help.html")


@frontend.route("/contact")
def contact():
    return render_template("contact.html")


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
# ERROR HANDLERS
# ==========================================
@frontend.app_errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


@frontend.app_errorhandler(500)
def internal_server_error(e):
    return render_template("500.html", error=str(e)), 500