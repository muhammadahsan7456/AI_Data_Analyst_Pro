"""
Production WSGI Entry Point for AI Data Analyst Pro
Runs high-concurrency production WSGI server using Waitress.
"""

import os
import sys
from run import app

if __name__ == "__main__":
    # Force UTF-8 on stdout/stderr before anything prints. Without this the startup
    # banner below (and every other emoji-containing log line in the app) raises
    # UnicodeEncodeError and kills the server before it ever binds a port - Windows
    # consoles default to cp1252, and so does a redirected stdout under a process
    # manager (systemd/supervisor/nssm) on any OS. run.py already did this; this
    # entrypoint, the one actually used in production, did not.
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    try:
        from waitress import serve
        port = int(os.getenv("PORT", 5000))
        print(f"🚀 Launching Enterprise Production Waitress WSGI Server on http://0.0.0.0:{port} ...")
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        print("⚠️ Waitress WSGI server not installed. Falling back to default Flask server...")
        app.run(host="0.0.0.0", port=5000, debug=False)
