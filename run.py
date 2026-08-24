import os
import sys
import time

# Auto-detect & add local .venv site-packages if executed via global python interpreter
venv_site_packages = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "Lib", "site-packages")
if os.path.exists(venv_site_packages) and venv_site_packages not in sys.path:
    sys.path.insert(0, venv_site_packages)

# Ensure workspace root is in sys.path for direct script execution
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
from flask import Flask, session, g, request

load_dotenv()

from database.connection import init_db, get_db_cursor
from database.queries import get_user_by_id, get_user_settings
from frontend.routes import frontend
from auth import auth_bp
from admin import admin_bp

# Auto-initialize database tables and background scheduler thread on launch
try:
    init_db()
    from utils.scheduler import start_scheduler
    start_scheduler()
except Exception as err:
    print("Database & Scheduler init notice:", err)

from datetime import timedelta
from flask import Flask, session, g, request, redirect, url_for, flash

app = Flask(__name__)

# Configure Secret Key for Sessions & 1-Hour Permanent Lifetime
app.secret_key = os.getenv("SECRET_KEY", "super-secret-enterprise-key-ai-analyst-2026")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=1)

# Set 500 MB Maximum Upload Size Limit
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

# Register Blueprints
app.register_blueprint(frontend)
app.register_blueprint(auth_bp, url_prefix="/auth")
app.register_blueprint(admin_bp)


import gzip
import io

# Request-Level High Speed Context Caching & 1-Hour Inactivity Session Timeout
@app.before_request
def load_user_context():
    # Bypass DB overhead for static files & favicons
    if request.path.startswith("/static/") or request.path == "/favicon.ico":
        return

    user_id = session.get("user_id")
    g.current_user = None
    g.user_settings = None

    if user_id:
        # Enforce 1-Hour (3,600 Seconds) Inactivity Security Timeout
        now_ts = int(time.time())
        last_act = session.get("last_activity")
        if last_act and (now_ts - last_act > 3600):
            session.clear()
            flash("Your session has expired due to 1 hour of inactivity. Please log in again to continue safely.", "warning")
            return redirect(url_for("auth.login"))

        session["last_activity"] = now_ts

        try:
            with get_db_cursor() as cursor:
                cursor.execute(get_user_by_id(), (user_id,))
                g.current_user = cursor.fetchone()

                cursor.execute(get_user_settings(), (user_id,))
                g.user_settings = cursor.fetchone()

                if g.current_user and len(g.current_user) > 13:
                    g.user_role = g.current_user[13]
                else:
                    g.user_role = "Analyst"
        except Exception:
            g.user_role = "Analyst"


# Global Context Processor for Templates
@app.context_processor
def inject_user_context():
    return dict(
        current_user=getattr(g, 'current_user', None),
        user_settings=getattr(g, 'user_settings', None),
        is_logged_in=bool(session.get("user_id"))
    )


# Jinja Template Filter for Executive AI Summary Formatting & 12-Hour AM/PM DateTime
from ai.data_summary import format_ai_explanation
from utils.helpers import format_12hr_datetime

@app.template_filter("format_explanation")
def format_explanation_filter(text):
    return format_ai_explanation(text)

@app.template_filter("datetime_12hr")
def datetime_12hr_filter(val):
    return format_12hr_datetime(val)


# High Speed Gzip Response Compression & HTTP Performance Caching Headers
@app.after_request
def add_performance_headers(response):
    if request.path.startswith("/static/"):
        # Browser Cache Static Assets for 1 Year
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        # Dynamic Responses
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"

    # Transparent Gzip Compression for HTML, CSS, JS, JSON (> 500 bytes)
    accept_encoding = request.headers.get("Accept-Encoding", "").lower()
    if (
        "gzip" in accept_encoding and
        response.status_code == 200 and
        not response.direct_passthrough and
        response.content_length is not None and
        response.content_length > 500 and
        "Content-Encoding" not in response.headers
    ):
        try:
            gzip_buffer = io.BytesIO()
            with gzip.GzipFile(mode="wb", fileobj=gzip_buffer, compresslevel=6) as gz:
                gz.write(response.get_data())
            response.set_data(gzip_buffer.getvalue())
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Content-Length"] = len(response.get_data())
            response.headers["Vary"] = "Accept-Encoding"
        except Exception:
            pass

    return response


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    port = int(os.getenv("PORT", 5000))
    print(f"[SERVER] Starting AI Data Analyst Pro High Performance Server on http://127.0.0.1:{port} ...")
    try:
        from waitress import serve
        serve(app, host="127.0.0.1", port=port, threads=16)
    except Exception:
        app.run(debug=True, port=port)