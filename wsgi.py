"""
Production WSGI Entry Point for AI Data Analyst Pro
Runs high-concurrency production WSGI server using Waitress.
"""

import os
import sys
from run import app

if __name__ == "__main__":
    try:
        from waitress import serve
        port = int(os.getenv("PORT", 5000))
        print(f"🚀 Launching Enterprise Production Waitress WSGI Server on http://0.0.0.0:{port} ...")
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        print("⚠️ Waitress WSGI server not installed. Falling back to default Flask server...")
        app.run(host="0.0.0.0", port=5000, debug=False)
