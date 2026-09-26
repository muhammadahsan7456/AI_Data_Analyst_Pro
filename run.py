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
from flask import Flask, session, g, request, redirect, url_for, flash

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

app = Flask(__name__)

# Configure Secret Key for Sessions & 1-Hour Permanent Lifetime
_secret_key = os.getenv("SECRET_KEY", "").strip()
if not _secret_key:
    import secrets as _secrets
    _secret_key = _secrets.token_hex(32)
    print(
        "[SECURITY WARNING] SECRET_KEY is not set in .env - generated a random "
        "one-time key for this process instead. All existing sessions are now "
        "invalid and will not survive a restart. Set SECRET_KEY in .env for "
        "stable, production-safe sessions."
    )
app.secret_key = _secret_key
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=1)

# Set 500 MB Maximum Upload Size Limit
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

# Hardened Session Cookie Policy
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Only require HTTPS-only cookies once the app is actually served over HTTPS (e.g. behind
# a TLS-terminating reverse proxy in production) - forcing this on plain HTTP would silently
# break login by preventing the browser from ever sending the session cookie back.
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FORCE_SECURE_COOKIES", "false").strip().lower() == "true"

# CSRF Protection (Flask-WTF) - validates a token on every state-changing POST/PUT/PATCH/DELETE
# request app-wide, preventing malicious third-party sites from forging requests using a
# logged-in user's (or admin's) browser session.
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
csrf = CSRFProtect(app)


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    """
    A token can legitimately go stale (a form left open across a server restart, a
    session that expired, a page restored from the browser's back/forward cache) -
    none of that is an attack, so showing Flask-WTF's raw "400 Bad Request" page is
    the wrong response. This sends the user back to try again with a plain-language
    explanation instead, on whichever page they were actually on.
    """
    flash("Your session has expired or the page was open for too long. Please try again.", "warning")
    # Only trust the referrer if it points back at this same site - a cross-site request
    # (which is exactly the scenario CSRF protection exists to catch) could otherwise
    # carry an attacker-controlled Referer header and turn this into an open redirect.
    from urllib.parse import urlparse
    ref = request.referrer
    if ref and urlparse(ref).netloc == urlparse(request.host_url).netloc:
        return redirect(ref)
    return redirect(url_for("frontend.home"))

# Register Blueprints
app.register_blueprint(frontend)
app.register_blueprint(auth_bp, url_prefix="/auth")
app.register_blueprint(admin_bp)


import gzip
import io

# Request-Level High Speed Context Caching & 1-Hour Inactivity Session Timeout
@app.before_request
def load_user_context():
    # Bypass DB overhead for static files, favicons, and auth routes
    if request.path.startswith("/static/") or request.path == "/favicon.ico" or request.path.startswith("/auth/"):
        return

    is_admin_path = request.path.startswith("/admin")
    now_ts = int(time.time())

    g.current_user = None
    g.user_settings = None
    g.admin_user = None

    # 1. User Session Context (User Portal / Dashboard)
    user_id = session.get("user_id")
    if user_id:
        last_act = session.get("last_activity")
        if last_act and (now_ts - last_act > 3600):
            for k in ["user_id", "user_name", "user_email", "user_role", "session_token", "last_activity"]:
                session.pop(k, None)
            flash("Your user session has expired due to 1 hour of inactivity. Please log in again.", "warning")
            if not is_admin_path and not request.path.startswith("/auth"):
                return redirect(url_for("auth.login"))
        else:
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

    # 2. Super Admin Session Context (Super Admin Console)
    admin_id = session.get("admin_user_id")
    if admin_id:
        admin_last_act = session.get("admin_last_activity")
        if admin_last_act and (now_ts - admin_last_act > 3600):
            for k in ["admin_user_id", "admin_user_name", "admin_user_email", "admin_user_role", "admin_session_token", "admin_last_activity"]:
                session.pop(k, None)
            flash("Your Super Admin session has expired due to 1 hour of inactivity.", "warning")
            if is_admin_path and request.path != "/admin/login":
                return redirect(url_for("admin.admin_login"))
        else:
            session["admin_last_activity"] = now_ts
            try:
                with get_db_cursor() as cursor:
                    cursor.execute(get_user_by_id(), (admin_id,))
                    g.admin_user = cursor.fetchone()
            except Exception:
                pass


from utils.subscription import get_user_subscription_info

# Global Context Processor for Templates
@app.context_processor
def inject_user_context():
    user_id = session.get("user_id")
    sub_info = get_user_subscription_info(user_id) if user_id else {}

    pending_payments_count = 0
    if session.get("admin_user_id"):
        try:
            with get_db_cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM Payments WHERE Status IN ('Pending', 'Pending_Review')")
                pending_payments_count = cursor.fetchone()[0] or 0
        except Exception:
            pass

    return dict(
        current_user=getattr(g, 'current_user', None),
        user_settings=getattr(g, 'user_settings', None),
        admin_user=getattr(g, 'admin_user', None),
        is_logged_in=bool(user_id),
        is_admin_logged_in=bool(session.get("admin_user_id")),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "500")),
        subscription_info=sub_info,
        pending_payments_count=pending_payments_count
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


# HTTP Performance Caching & Security Headers
@app.after_request
def add_performance_headers(response):
    if request.path.startswith("/static/") or request.path == "/favicon.ico":
        # Static assets (CSS/JS/images) are safe and fast to let the browser cache -
        # forcing no-cache on these was making every page reload every asset every time.
        response.headers["Cache-Control"] = "public, max-age=86400"
    else:
        # Dynamic/authenticated pages may contain per-user data and must never be cached.
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Content-Security-Policy - scoped to the exact external origins this app actually
    # loads from (cdnjs for Font Awesome, Google Fonts, the Plotly chart CDN). 'unsafe-inline'
    # is kept for script/style because this app has inline <script>/style="..." attributes
    # throughout every template - removing it would require adding a nonce to every single
    # one, which is a much larger, riskier change than this audit's scope. This is still a
    # real improvement over no CSP at all (blocks loading executable content from anywhere
    # else, restricts framing/forms/base-uri). The Google AdSense placeholder in
    # landing_base.html isn't in script-src yet since it uses a fake publisher ID and isn't
    # actually active - widen this if/when that's turned on with a real ID.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.plot.ly; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'self'"
    )
    # microphone=(self) allows this site's own pages to use the mic for the Voice AI
    # feature - a blanket microphone=() (my earlier default) blocks it at the browser
    # level entirely, before the user's own permission choice is even consulted.
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(self), camera=()"
    if app.config.get("SESSION_COOKIE_SECURE"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # Gzip-compress text-based responses (HTML/CSS/JS/JSON) when the client supports it -
    # meaningfully cuts transfer size/time for pages with large dataset preview tables.
    compressible = ("text/html", "text/css", "application/javascript", "text/javascript", "application/json")
    content_type = (response.content_type or "").split(";")[0].strip()
    if (
        "gzip" in request.headers.get("Accept-Encoding", "")
        and content_type in compressible
        and "Content-Encoding" not in response.headers
        and not response.direct_passthrough
        and response.content_length is not None
        and response.content_length > 500
    ):
        compressed = gzip.compress(response.get_data(), compresslevel=6)
        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Vary"] = "Accept-Encoding"

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
    except ImportError:
        # waitress itself isn't installed - fall back to Flask's own server, but NEVER
        # with debug=True: that turns on the interactive Werkzeug debugger, which lets
        # anyone who triggers an unhandled exception in the browser run arbitrary Python
        # on the server. A missing dependency should degrade to a slower server, not an
        # open remote-code-execution hole.
        print("[SERVER WARNING] waitress not installed - falling back to Flask's built-in server (debug mode OFF).")
        app.run(debug=False, port=port)