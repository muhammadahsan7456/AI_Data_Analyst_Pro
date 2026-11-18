"""
Production WSGI entry point for AI Data Analyst Pro (python wsgi.py).

On the VM it binds 127.0.0.1 behind Nginx and trusts that one local proxy hop for
X-Forwarded-* headers. In Docker (/.dockerenv present) it binds 0.0.0.0 as before.
Set BIND_HOST to override. Run exactly ONE process: the app owns a scheduler thread
and in-process upload jobs.
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
        # Outside a container this process sits behind Nginx on the same VM, so it binds
        # to loopback only and trusts exactly that one proxy hop for X-Forwarded-For/
        # -Proto/-Host. Without this, Waitress strips those headers: every login/audit IP
        # was recorded as 127.0.0.1 and request.scheme was "http" even on HTTPS. Inside
        # Docker (unchanged) it keeps binding 0.0.0.0. BIND_HOST overrides either default.
        in_container = os.path.exists("/.dockerenv")
        host = os.getenv("BIND_HOST", "").strip() or ("0.0.0.0" if in_container else "127.0.0.1")
        options = dict(threads=32, connection_limit=300, channel_timeout=120)
        if host in ("127.0.0.1", "localhost"):
            options.update(
                trusted_proxy="127.0.0.1",
                trusted_proxy_count=1,
                trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"},
                clear_untrusted_proxy_headers=True,
            )
        print(f"Launching Waitress on http://{host}:{port} ...")
        serve(app, host=host, port=port, **options)
    except ImportError:
        print("Waitress is not installed. Falling back to Flask's server (debug off)...")
        app.run(host="127.0.0.1", port=int(os.getenv("PORT", 5000)), debug=False)
