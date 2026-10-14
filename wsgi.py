"""
Docker/container WSGI entry point for AI Data Analyst Pro (see Dockerfile's CMD).

The actual production deployment (EC2 + nginx + systemd) runs run.py directly,
not this file - binding to 0.0.0.0 here is correct for a container, where
run.py's 127.0.0.1-only binding (and the trusted_proxy config that assumes a
reverse proxy on that same loopback) would not be reachable from outside the
container at all.
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
        # Thread/connection settings kept in sync with run.py's tuning (see that file's
        # comment for the full reasoning) - threads=8 here was stale and would under-serve
        # concurrent requests compared to the entry point actually used in production.
        serve(app, host="0.0.0.0", port=port, threads=32, connection_limit=300, channel_timeout=120)
    except ImportError:
        print("⚠️ Waitress WSGI server not installed. Falling back to default Flask server...")
        app.run(host="0.0.0.0", port=5000, debug=False)
