"""
Process start time and a lightweight, thread-safe request/error counter.

Lives in its own module (not inside run.py) specifically so other modules - like
admin/routes.py's Infrastructure Health panel - can import it without re-executing
run.py's top-level code (Flask app creation, init_db(), start_scheduler(), blueprint
registration, etc.) a second time in the same process. run.py imports and updates this;
anything else only ever reads it.
"""

import time
import threading

APP_START_TIME = time.time()

_lock = threading.Lock()
_stats = {"total": 0, "errors": 0}


def record_request(status_code: int):
    with _lock:
        _stats["total"] += 1
        if status_code >= 500:
            _stats["errors"] += 1


def get_request_stats() -> dict:
    with _lock:
        return dict(_stats)
