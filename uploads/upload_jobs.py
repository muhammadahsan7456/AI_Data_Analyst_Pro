"""
Background ingestion jobs for large dataset uploads.

A million-row file took ~83 seconds to ingest, and the browser sat on a hanging POST for
every one of them with no indication that anything was happening. Anything that slow
belongs off the request thread, so large files are now handed to a worker and the POST
returns immediately with a job id the page can poll for real progress.

Small files keep their existing fully synchronous behaviour - the response shape the
upload page already knows how to render - because for those the round trip is faster
than a single poll interval anyway.
"""

import os
import tempfile
import threading
import time
import uuid

# Files at or above this size are ingested in the background. Below it, the synchronous
# path is quicker end to end than setting up a job and polling for it. Configurable so
# the threshold can be tuned per deployment (and so the old fully-synchronous behaviour
# can be restored by setting it very high, which is also how the before/after timings
# for this change were measured on one server).
ASYNC_THRESHOLD_MB = float(os.getenv("UPLOAD_ASYNC_THRESHOLD_MB", "8"))
ASYNC_THRESHOLD_BYTES = int(ASYNC_THRESHOLD_MB * 1024 * 1024)

# Finished jobs are kept this long so a slow or reloaded page can still read the result.
JOB_RETENTION_SECONDS = 30 * 60

_jobs = {}
_lock = threading.Lock()


def _now():
    return time.time()


def create_job(user_id: int, filename: str, size_bytes: int) -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "user_id": user_id,
            "filename": filename,
            "size_bytes": size_bytes,
            "status": "queued",          # queued | processing | done | error
            "stage": "Upload received, queued for processing...",
            "rows_done": 0,
            "rows_total": None,          # unknown until the file has been read
            "percent": 0,
            "result": None,
            "error": None,
            "created_at": _now(),
            "finished_at": None,
        }
    return job_id


def update_job(job_id: str, **fields):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(fields)
        # Derive percent from rows when the total is known. Capped just under 100 so the
        # bar only reaches 100% when the job is genuinely finished, not when the last
        # chunk is inserted but metadata/indexes are still being written.
        total = job.get("rows_total")
        done = job.get("rows_done") or 0
        if total:
            job["percent"] = min(99, int(done * 100 / total))
        if job.get("status") == "done":
            job["percent"] = 100


def get_job(job_id: str, user_id: int = None):
    """Return a job, but only to the user who created it."""
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        if user_id is not None and job.get("user_id") != user_id:
            return None
        return dict(job)


def _purge_expired():
    cutoff = _now() - JOB_RETENTION_SECONDS
    with _lock:
        for jid in [j for j, v in _jobs.items()
                    if v.get("finished_at") and v["finished_at"] < cutoff]:
            _jobs.pop(jid, None)


def spool_to_disk(uploaded_file, filename: str) -> str:
    """
    Write the upload to a temp file so the worker can read it after the request ends.

    The request's file object is tied to the request; once the POST returns, Werkzeug is
    free to close it. The worker needs its own copy on disk.
    """
    suffix = os.path.splitext(filename)[1] or ".dat"
    fd, path = tempfile.mkstemp(prefix="aidap_upload_", suffix=suffix)
    os.close(fd)
    uploaded_file.seek(0)
    with open(path, "wb") as out:
        while True:
            chunk = uploaded_file.read(4 * 1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return path


class SpooledUpload:
    """
    Minimal stand-in for a Werkzeug FileStorage, backed by a temp file on disk.

    process_file_upload() only ever needs .filename, .seek(), .read() and .save(), so
    this is everything the pipeline touches - no changes needed inside it.
    """

    def __init__(self, path: str, filename: str):
        self._path = path
        self.filename = filename
        self._fh = open(path, "rb")

    def read(self, *a):
        return self._fh.read(*a)

    def seek(self, *a):
        return self._fh.seek(*a)

    def tell(self):
        return self._fh.tell()

    def save(self, dst):
        self.seek(0)
        with open(dst, "wb") as out:
            while True:
                chunk = self._fh.read(4 * 1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass

    def cleanup(self):
        self.close()
        try:
            os.unlink(self._path)
        except Exception:
            pass


def start_ingest_job(job_id: str, spooled_files, user_id: int, tags: str,
                     on_success=None):
    """
    Run the normal ingestion pipeline on a worker thread.

    `spooled_files` is a list of (temp_path, filename) - the upload form accepts several
    files at once, and they are ingested one after another under a single job so the page
    has one thing to poll.

    `on_success(results, errors)` runs on the worker once everything is done, for the
    bookkeeping the route used to do inline (cache invalidation, audit log). It is passed
    in rather than imported here to keep this module free of route imports.
    """

    def worker():
        results, errors = [], []
        opened = []
        try:
            update_job(job_id, status="processing", stage="Reading and parsing file...")
            from uploads.multi_loader import process_file_upload

            n = len(spooled_files)
            rows_before = 0
            for idx, (temp_path, filename) in enumerate(spooled_files, start=1):
                spooled = SpooledUpload(temp_path, filename)
                opened.append(spooled)
                prefix = f"({idx}/{n}) " if n > 1 else ""

                def progress(rows_done, rows_total=None, stage=None, _p=prefix,
                             _base=rows_before):
                    fields = {"rows_done": _base + (rows_done or 0)}
                    if rows_total is not None:
                        fields["rows_total"] = _base + rows_total
                    if stage:
                        fields["stage"] = _p + stage
                    update_job(job_id, **fields)

                ok, res = process_file_upload(spooled, user_id=user_id, tags=tags,
                                              progress_cb=progress)
                if ok:
                    results.append(res)
                    rows_before += int(res.get("rows") or 0)
                else:
                    errors.append(str(res))

            if results:
                update_job(job_id, status="done", stage="Complete", result=results[0],
                           results=results, errors=errors, finished_at=_now())
                if on_success:
                    try:
                        on_success(results, errors)
                    except Exception as cb_err:
                        print("[UPLOAD JOB] post-success hook failed:", cb_err)
            else:
                update_job(job_id, status="error", stage="Failed",
                           error="; ".join(errors) or "Upload failed.",
                           finished_at=_now())
        except Exception as err:
            update_job(job_id, status="error", stage="Failed", error=str(err),
                       finished_at=_now())
        finally:
            for sp in opened:
                sp.cleanup()
            for temp_path, _ in spooled_files:
                if not os.path.exists(temp_path):
                    continue
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
            _purge_expired()

    t = threading.Thread(target=worker, name=f"ingest-{job_id[:8]}", daemon=True)
    t.start()
    return t
