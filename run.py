import os
import sys
import time

# Guarantee UTF-8 stream output across Windows console and process runners
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

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

# Hard request-body ceiling. Set a few MB above the 500 MB file-size limit the upload
# views enforce (frontend.upload / frontend.api_upload_file) - multipart form-data adds
# boundary/header overhead and other fields (csrf_token, tags) on top of the raw file
# bytes, so a legitimate exactly-500MB file must not get rejected here before the app's
# own, more precise file-size check ever runs.
app.config["MAX_CONTENT_LENGTH"] = 508 * 1024 * 1024

# Hardened Session Cookie Policy
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Only require HTTPS-only cookies once the app is actually served over HTTPS (e.g. behind
# a TLS-terminating reverse proxy in production) - forcing this on plain HTTP would silently
# break login by preventing the browser from ever sending the session cookie back.
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FORCE_SECURE_COOKIES", "false").strip().lower() == "true"

# Google AdSense publisher ID. The templates previously hardcoded the literal placeholder
# "ca-pub-XXXXXXXXXXXXXXXX", which made every public page fire real ad requests against an
# ID that can never resolve - they hang until they time out, on every visit. The ad slots
# stay exactly where they are; the loader script is simply not emitted until a real ID is
# configured here, at which point AdSense works with no further template changes.
app.config["ADSENSE_CLIENT_ID"] = os.getenv("ADSENSE_CLIENT_ID", "").strip()

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


@app.errorhandler(413)
def handle_request_too_large(e):
    """
    MAX_CONTENT_LENGTH (500 MB, set above) rejects an oversized request at the
    WSGI/Werkzeug layer before it ever reaches the upload view, so the app-level
    "exceeds 500 MB" check in frontend.upload / frontend.api_upload_file never runs
    for a file well over the cap - without this handler the user would see Werkzeug's
    bare "413 Request Entity Too Large" page instead of the same clear message.
    """
    msg = "This file exceeds the maximum supported file size of 500 MB. Please upload a smaller file."
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        from flask import jsonify
        return jsonify({"success": False, "error": msg}), 413
    flash(msg, "error")
    from urllib.parse import urlparse
    ref = request.referrer
    if ref and urlparse(ref).netloc == urlparse(request.host_url).netloc:
        return redirect(ref)
    return redirect(url_for("frontend.upload"))


# Register Blueprints
app.register_blueprint(frontend)
app.register_blueprint(auth_bp, url_prefix="/auth")
app.register_blueprint(admin_bp)

# Top-level convenience routes to prevent 404 on direct /login, /register, /signup navigation
@app.route("/login")
def top_login():
    return redirect(url_for("auth.login", **request.args))

@app.route("/register")
def top_register():
    return redirect(url_for("auth.register", **request.args))

@app.route("/signup")
def top_signup():
    return redirect(url_for("auth.register", **request.args))


import gzip
import io

# Pre-authentication flows only - these never need g.current_user since there's no
# logged-in user yet (or, for logout, there's about to not be one). The old blanket
# "/auth/" skip in load_user_context() below also caught the POST-authentication
# account-management pages under this same blueprint (profile, settings, security,
# change-password), which DO need it: base.html's sidebar footer gates its "Sign Out"
# button on `is_logged_in and current_user` (line ~130), so on every one of those pages a
# genuinely logged-in user saw "Sign In"/"Create Account" instead - found live while
# visually reviewing these pages for a separate issue.
AUTH_SKIP_PREFIXES = (
    "/auth/check-availability", "/auth/register", "/auth/signup",
    "/auth/verify-otp", "/auth/resend-otp", "/auth/api/check-user",
    "/auth/verify-email", "/auth/resend-verification", "/auth/login",
    "/auth/forgot-password", "/auth/reset-password", "/auth/logout",
)


# Request-Level High Speed Context Caching & 1-Hour Inactivity Session Timeout
@app.before_request
def load_user_context():
    # Bypass DB overhead for static files, favicons, and PRE-authentication auth routes
    # (not account-management pages under the same blueprint - see AUTH_SKIP_PREFIXES).
    if request.path.startswith("/static/") or request.path == "/favicon.ico" or request.path.startswith(AUTH_SKIP_PREFIXES):
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
    from utils.request_stats import record_request
    record_request(response.status_code)

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
    # else, restricts framing/forms/base-uri). The Google AdSense origins are listed so the
    # ad tag works once ADSENSE_CLIENT_ID is set; while that env var is empty no AdSense
    # script is emitted at all, so nothing is actually loaded from these origins.
    ADSENSE_SCRIPT = (
        "https://pagead2.googlesyndication.com https://partner.googleadservices.com "
        "https://tpc.googlesyndication.com https://adservice.google.com"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.plot.ly {ADSENSE_SCRIPT}; "
        # fonts.googleapis.com is deliberately absent: the font stylesheet is now served
        # from static/css/fonts.css, so nothing loads styles from Google any more. The
        # .woff2 files it references still come from fonts.gstatic.com, hence font-src.
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://pagead2.googlesyndication.com https://googleads.g.doubleclick.net; "
        "frame-src 'self' https://googleads.g.doubleclick.net https://tpc.googlesyndication.com; "
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

    # Don't advertise the exact WSGI server; it tells an attacker which CVEs to try first.
    response.headers["Server"] = "AI Data Analyst Pro"

    # Gzip-compress text-based responses (HTML/CSS/JS/JSON) when the client supports it -
    # meaningfully cuts transfer size/time for pages with large dataset preview tables.
    compressible = ("text/html", "text/css", "application/javascript", "text/javascript", "application/json")
    content_type = (response.content_type or "").split(";")[0].strip()
    wants_gzip = (
        "gzip" in request.headers.get("Accept-Encoding", "")
        and content_type in compressible
        and "Content-Encoding" not in response.headers
        and response.content_length is not None
        and response.content_length > 500
    )
    # Static files (CSS/JS) come back from send_from_directory as a file wrapper with
    # direct_passthrough set, which used to skip this block entirely - so every page was
    # shipping style.css/landing.css uncompressed (~60KB each) while the HTML around them
    # was gzipped. Buffering them is safe: the size cap below keeps it to small text assets,
    # and large binary downloads (exports, screenshots) aren't in `compressible` anyway.
    MAX_PASSTHROUGH_GZIP = 2 * 1024 * 1024
    if wants_gzip and response.direct_passthrough:
        if response.content_length <= MAX_PASSTHROUGH_GZIP:
            response.direct_passthrough = False
        else:
            wants_gzip = False

    if wants_gzip:
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
        # Waitress strips X-Forwarded-For/X-Real-IP by default from ANY client, trusted or
        # not - a real security default (nothing stops a random internet client sending a
        # fake X-Forwarded-For otherwise), but it means auth.security.get_client_ip()'s
        # header reading never saw real data: every captured login/audit IP was Waitress's
        # own loopback peer address, 127.0.0.1, for every user - confirmed live (spoofed
        # X-Forwarded-For arrived at the socket, per req.header_items(), but was still
        # 127.0.0.1 by the time Flask's request.headers saw it). Since this process only
        # binds to 127.0.0.1 itself, the only thing that can ever connect to it directly is
        # a reverse proxy already running on this same machine (nginx, a cloud load
        # balancer's local agent, etc.) - trusting exactly that one hop from loopback is
        # the correct, narrow fix for this deployment shape, not a blanket "trust
        # everyone" that would reopen the spoofing hole.
        # threads=16 meant only 16 requests could be in flight at once - and most request
        # time here is spent waiting on RDS or an external AI API call, not on CPU work, so
        # a handful of concurrently slow requests (a multi-second AI query, a large upload)
        # could already exhaust the pool and leave the next visitor queued behind them, even
        # with no CPU pressure at all. Raised to a level suited for I/O-bound work on a
        # small instance; connection_limit/channel_timeout made explicit instead of relying
        # on Waitress's defaults, so queuing behavior under load is a deliberate choice here,
        # not an accident of whatever those defaults happen to be.
        serve(
            app, host="127.0.0.1", port=port, threads=32,
            connection_limit=300,
            channel_timeout=120,
            trusted_proxy="127.0.0.1",
            trusted_proxy_count=1,
            trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"},
            clear_untrusted_proxy_headers=True,
        )
    except ImportError:
        # waitress itself isn't installed - fall back to Flask's own server, but NEVER
        # with debug=True: that turns on the interactive Werkzeug debugger, which lets
        # anyone who triggers an unhandled exception in the browser run arbitrary Python
        # on the server. A missing dependency should degrade to a slower server, not an
        # open remote-code-execution hole.
        print("[SERVER WARNING] waitress not installed - falling back to Flask's built-in server (debug mode OFF).")
        app.run(debug=False, port=port)