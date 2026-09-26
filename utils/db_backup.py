"""
Automated Daily Database Backup

Runs a native SQL Server BACKUP DATABASE once per day via the background scheduler,
so the live database has a real recovery point without anyone having to remember to
click a manual "Backup Now" button. Old backups are rotated out after a retention
window so the backup folder doesn't grow unbounded.
"""

import os
import re
import glob
import pyodbc
from datetime import datetime
from database.connection import get_connection_string

RETENTION_DAYS = int(os.getenv("DB_BACKUP_RETENTION_DAYS", "7"))


def _get_backup_dir():
    """
    Defaults to a project-local db_backups/ folder rather than SQL Server's own default
    backup directory - that default path is locked down to the SQL Server service
    account only (verified: even a local admin PowerShell session gets Access Denied
    listing it), so the app itself could never read back or rotate what it backed up.
    The SQL Server service account (NT SERVICE\\MSSQLSERVER) is granted write access to
    this project folder once via icacls; override with DB_BACKUP_DIR if needed.
    """
    custom = os.getenv("DB_BACKUP_DIR")
    if custom:
        os.makedirs(custom, exist_ok=True)
        return custom

    default_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db_backups")
    os.makedirs(default_dir, exist_ok=True)
    return default_dir


def run_database_backup(db_name=None):
    """
    Executes a compressed full backup of the live database. Returns the backup file
    path on success, or None on failure (errors are printed, never raised, so a backup
    failure never takes down the scheduler thread or any other background job).
    """
    db_name = db_name or os.getenv("DB_NAME", "AI_Data_Analyst_Pro")
    backup_dir = _get_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{db_name}_backup_{timestamp}.bak"
    full_path = os.path.join(backup_dir, filename)

    conn = None
    try:
        # BACKUP DATABASE refuses to run inside a transaction, and pyodbc connections
        # default to autocommit=False (an implicit transaction starts on first
        # statement) - get_db_cursor()'s shared connection hits exactly that error, so
        # this uses its own dedicated autocommit connection instead.
        conn = pyodbc.connect(get_connection_string(), timeout=10, autocommit=True)
        cursor = conn.cursor()
        cursor.execute(f"""
            BACKUP DATABASE [{db_name}]
            TO DISK = N'{full_path}'
            WITH INIT, COMPRESSION, STATS = 25
        """)
        print(f"[DB BACKUP] Success: {full_path}")
        _cleanup_old_backups(backup_dir, db_name)
        return full_path
    except Exception as e:
        print("[DB BACKUP] Failed:", e)
        return None
    finally:
        if conn is not None:
            conn.close()


def _cleanup_old_backups(backup_dir, db_name):
    """
    Deletes backups older than RETENTION_DAYS that match our own naming pattern only -
    never touches files it didn't create, in case the folder is shared with other jobs.
    """
    try:
        pattern = os.path.join(backup_dir, f"{db_name}_backup_*.bak")
        now = datetime.now()
        for filepath in glob.glob(pattern):
            m = re.search(r"_backup_(\d{8})_\d{6}\.bak$", filepath)
            if not m:
                continue
            file_date = datetime.strptime(m.group(1), "%Y%m%d")
            if (now - file_date).days > RETENTION_DAYS:
                os.remove(filepath)
                print(f"[DB BACKUP] Rotated out old backup: {filepath}")
    except Exception as e:
        print("[DB BACKUP] Cleanup error:", e)
