import os
import sys

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

# Auto-initialize database tables on launch
try:
    init_db()
except Exception as err:
    print("Database init notice:", err)

app = Flask(__name__)

# Configure Secret Key for Sessions
app.secret_key = os.getenv("SECRET_KEY", "super-secret-enterprise-key-ai-analyst-2026")

# Set 500 MB Maximum Upload Size Limit
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

# Register Blueprints
app.register_blueprint(frontend)
app.register_blueprint(auth_bp, url_prefix="/auth")


# Request-Level High Speed Context Caching
@app.before_request
def load_user_context():
    user_id = session.get("user_id")
    g.current_user = None
    g.user_settings = None

    if user_id:
        try:
            with get_db_cursor() as cursor:
                cursor.execute(get_user_by_id(), (user_id,))
                g.current_user = cursor.fetchone()

                cursor.execute(get_user_settings(), (user_id,))
                g.user_settings = cursor.fetchone()
        except Exception:
            pass


# Global Context Processor for Templates
@app.context_processor
def inject_user_context():
    return dict(
        current_user=getattr(g, 'current_user', None),
        user_settings=getattr(g, 'user_settings', None),
        is_logged_in=bool(session.get("user_id"))
    )


# Jinja Template Filter for Executive AI Summary Formatting
from ai.data_summary import format_ai_explanation

@app.template_filter("format_explanation")
def format_explanation_filter(text):
    return format_ai_explanation(text)


# High Speed HTTP Performance & Static Asset Caching Headers
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
    return response


if __name__ == "__main__":
    print("🚀 Starting AI Data Analyst Pro High Performance Server on http://127.0.0.1:5000 ...")
    app.run(debug=True, port=5000)