import os
import sys
import re
import sqlite3
import pandas as pd
from contextlib import contextmanager
from dotenv import load_dotenv

# Ensure workspace root is in sys.path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

load_dotenv()

try:
    import pyodbc
except ImportError:
    pyodbc = None


class SQLiteCursorAdapter:
    def __init__(self, cursor):
        self._cursor = cursor
        self.last_inserted_id = None

    def execute(self, sql, params=()):
        s = str(sql)

        def replace_dateadd(match):
            unit = match.group(1).lower()
            num = match.group(2).strip()
            if unit in ("day", "days", "dd", "d"):
                unit_str = "days"
            elif unit in ("minute", "minutes", "mi", "n"):
                unit_str = "minutes"
            elif unit in ("hour", "hours", "hh"):
                unit_str = "hours"
            elif unit in ("month", "months", "mm", "m"):
                unit_str = "months"
            elif unit in ("second", "seconds", "ss", "s"):
                unit_str = "seconds"
            elif unit in ("year", "years", "yy", "yyyy"):
                unit_str = "years"
            else:
                unit_str = "days"

            if num.startswith("-"):
                return f"datetime('now', '{num} {unit_str}')"
            else:
                return f"datetime('now', '+{num} {unit_str}')"

        s = re.sub(r"DATEADD\s*\(\s*(\w+)\s*,\s*(-?\d+)\s*,\s*(?:GETDATE\(\)|CURRENT_TIMESTAMP)\s*\)", replace_dateadd, s, flags=re.IGNORECASE)
        s = s.replace("GETDATE()", "CURRENT_TIMESTAMP")
        s = re.sub(r"ISNULL\s*\(", "COALESCE(", s, flags=re.IGNORECASE)
        s = re.sub(r"OUTPUT\s+INSERTED\.\w+", "", s, flags=re.IGNORECASE)

        top_match = re.search(r"SELECT\s+TOP\s+\(?(\d+)\)?\s+(.*)", s, flags=re.IGNORECASE | re.DOTALL)
        if top_match:
            limit_val = top_match.group(1)
            rest_sql = top_match.group(2)
            s = f"SELECT {rest_sql.strip()} LIMIT {limit_val}"

        self._cursor.execute(s, params or ())
        if self._cursor.lastrowid:
            self.last_inserted_id = self._cursor.lastrowid
        return self

    def fetchone(self):
        res = self._cursor.fetchone()
        if res is None and self.last_inserted_id is not None:
            ret = (self.last_inserted_id,)
            self.last_inserted_id = None
            return ret
        if res is not None:
            return tuple(res)
        return None

    def fetchall(self):
        return [tuple(r) for r in self._cursor.fetchall()]

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass


class SQLiteConnectionAdapter:
    def __init__(self, db_path=None):
        if not db_path:
            db_path = os.path.join(os.path.dirname(__file__), "AI_Data_Analyst_Pro_cloud.db")
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)

    def cursor(self):
        return SQLiteCursorAdapter(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def get_connection_string():
    """
    Construct SQL Server connection string dynamically from environment variables
    with fallback defaults for local development.
    """
    server = os.getenv("DB_SERVER", "DESKTOP-1C016AT")
    database = os.getenv("DB_NAME", "AI_Data_Analyst_Pro")
    driver = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")
    trusted = os.getenv("DB_TRUSTED_CONNECTION", "yes")
    user = os.getenv("DB_USER", "")
    password = os.getenv("DB_PASSWORD", "")

    if user and password:
        return (
            f"DRIVER={{{driver}}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"UID={user};"
            f"PWD={password};"
        )
    else:
        return (
            f"DRIVER={{{driver}}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"Trusted_Connection={trusted};"
        )


def get_connection():
    """
    Establish and return database connection (SQL Server primary, SQLite cloud fallback).
    """
    if pyodbc is not None:
        try:
            conn_str = get_connection_string()
            return pyodbc.connect(conn_str, timeout=3)
        except Exception:
            try:
                fallback_str = (
                    "DRIVER={SQL Server};"
                    "SERVER=DESKTOP-1C016AT;"
                    "DATABASE=AI_Data_Analyst_Pro;"
                    "Trusted_Connection=yes;"
                )
                return pyodbc.connect(fallback_str, timeout=3)
            except Exception:
                pass

    # Seamless cloud fallback to local SQLite database when SQL Server is unreachable
    return SQLiteConnectionAdapter()


@contextmanager
def get_db_cursor(commit=False):
    """
    Context manager for database connections and cursors to ensure proper cleanup.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        yield cursor
        if commit:
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def init_sqlite_db(conn):
    cursor = conn.cursor()
    tables = [
        '''CREATE TABLE IF NOT EXISTS Users (
            UserID INTEGER PRIMARY KEY AUTOINCREMENT,
            FirstName TEXT,
            LastName TEXT,
            Username TEXT UNIQUE,
            FullName TEXT NOT NULL,
            Email TEXT NOT NULL UNIQUE,
            PhoneNumber TEXT,
            Country TEXT,
            City TEXT,
            PasswordHash TEXT NOT NULL,
            ProfileImage TEXT,
            IsActive INTEGER NOT NULL DEFAULT 1,
            IsVerified INTEGER NOT NULL DEFAULT 0,
            Role TEXT NOT NULL DEFAULT 'Analyst',
            FailedLoginAttempts INTEGER DEFAULT 0,
            LockoutUntil TEXT,
            CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UpdatedAt TEXT
        )''',
        '''CREATE TABLE IF NOT EXISTS UserProfiles (
            ProfileID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL UNIQUE,
            Bio TEXT,
            Occupation TEXT,
            Company TEXT,
            Department TEXT,
            Designation TEXT,
            Website TEXT,
            LinkedIn TEXT,
            GitHub TEXT,
            Portfolio TEXT,
            ProfileImage TEXT,
            Timezone TEXT DEFAULT 'UTC',
            Language TEXT DEFAULT 'en',
            UpdatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS UserSettings (
            SettingID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL UNIQUE,
            Theme TEXT DEFAULT 'light',
            DateFormat TEXT DEFAULT 'YYYY-MM-DD',
            DefaultExportFormat TEXT DEFAULT 'csv',
            ChartPreference TEXT DEFAULT 'bar',
            DashboardPreference TEXT DEFAULT 'standard',
            UpdatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS EmailVerificationTokens (
            TokenID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            Token TEXT NOT NULL UNIQUE,
            ExpiresAt TEXT NOT NULL,
            IsUsed INTEGER NOT NULL DEFAULT 0,
            Attempts INTEGER NOT NULL DEFAULT 0,
            UsedAt TEXT,
            CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS PasswordResetTokens (
            TokenID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            Token TEXT NOT NULL UNIQUE,
            ExpiresAt TEXT NOT NULL,
            IsUsed INTEGER NOT NULL DEFAULT 0,
            Attempts INTEGER NOT NULL DEFAULT 0,
            UsedAt TEXT,
            CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS LoginHistory (
            LogID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            IPAddress TEXT,
            UserAgent TEXT,
            Browser TEXT,
            OS TEXT,
            Device TEXT,
            Status TEXT NOT NULL,
            CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS UserSessions (
            SessionID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            SessionToken TEXT NOT NULL UNIQUE,
            IPAddress TEXT,
            UserAgent TEXT,
            ExpiresAt TEXT NOT NULL,
            IsActive INTEGER NOT NULL DEFAULT 1,
            CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS AuditLogs (
            LogID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NULL,
            Action TEXT NOT NULL,
            Details TEXT,
            IPAddress TEXT,
            CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS Datasets (
            DatasetID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            DatasetName TEXT NOT NULL,
            OriginalFileName TEXT NOT NULL,
            FileType TEXT NOT NULL,
            TotalRows INTEGER DEFAULT 0,
            TotalColumns INTEGER DEFAULT 0,
            StorageSizeKB REAL DEFAULT 0.0,
            IsFavorite INTEGER DEFAULT 0,
            Tags TEXT,
            LastOpenedAt TEXT,
            UploadDate TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS QueryLogs (
            QueryID INTEGER PRIMARY KEY AUTOINCREMENT,
            DatasetID INTEGER NULL,
            UserQuestion TEXT NOT NULL,
            GeneratedSQL TEXT NOT NULL,
            ExecutionStatus TEXT NOT NULL,
            RowsReturned INTEGER DEFAULT 0,
            ExecutionTimeMS REAL DEFAULT 0.0,
            ConfidenceScore REAL DEFAULT 1.0,
            RetryCount INTEGER DEFAULT 0,
            CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS AINotifications (
            NotificationID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            Category TEXT NOT NULL,
            Title TEXT NOT NULL,
            Message TEXT NOT NULL,
            MetadataJson TEXT,
            IsRead INTEGER DEFAULT 0,
            CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )'''
    ]

    for t in tables:
        try:
            cursor.execute(t)
        except Exception as e:
            print("SQLite Table Creation Notice:", e)

    conn.commit()


def init_db():
    """
    Automatically create missing core system tables, enterprise auth columns, and non-clustered performance indexes.
    """
    conn = get_connection()
    if isinstance(conn, SQLiteConnectionAdapter):
        init_sqlite_db(conn)
        validate_smtp_config()
        return

    query = """
    IF OBJECT_ID('Users', 'U') IS NULL
    BEGIN
        CREATE TABLE Users (
            UserID INT IDENTITY(1,1) PRIMARY KEY,
            FirstName NVARCHAR(100) NULL,
            LastName NVARCHAR(100) NULL,
            Username NVARCHAR(100) NULL UNIQUE,
            FullName NVARCHAR(200) NOT NULL,
            Email NVARCHAR(255) NOT NULL UNIQUE,
            PhoneNumber NVARCHAR(50) NULL,
            Country NVARCHAR(100) NULL,
            City NVARCHAR(100) NULL,
            PasswordHash NVARCHAR(500) NOT NULL,
            ProfileImage NVARCHAR(500) NULL,
            IsActive BIT NOT NULL DEFAULT 1,
            IsVerified BIT NOT NULL DEFAULT 0,
            Role NVARCHAR(50) NOT NULL DEFAULT 'Analyst',
            FailedLoginAttempts INT DEFAULT 0,
            LockoutUntil DATETIME2 NULL,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE(),
            UpdatedAt DATETIME2 NULL
        );
    END;

    IF OBJECT_ID('UserProfiles', 'U') IS NULL
    BEGIN
        CREATE TABLE UserProfiles (
            ProfileID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL UNIQUE,
            Bio NVARCHAR(MAX) NULL,
            Occupation NVARCHAR(150) NULL,
            Company NVARCHAR(150) NULL,
            Department NVARCHAR(150) NULL,
            Designation NVARCHAR(150) NULL,
            Website NVARCHAR(255) NULL,
            LinkedIn NVARCHAR(255) NULL,
            GitHub NVARCHAR(255) NULL,
            Portfolio NVARCHAR(255) NULL,
            ProfileImage NVARCHAR(500) NULL,
            Timezone NVARCHAR(100) DEFAULT 'UTC',
            Language NVARCHAR(20) DEFAULT 'en',
            UpdatedAt DATETIME2 NOT NULL DEFAULT GETDATE(),
            CONSTRAINT FK_UserProfiles_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );
    END;

    IF OBJECT_ID('UserSettings', 'U') IS NULL
    BEGIN
        CREATE TABLE UserSettings (
            SettingID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL UNIQUE,
            Theme NVARCHAR(20) DEFAULT 'light',
            DateFormat NVARCHAR(30) DEFAULT 'YYYY-MM-DD',
            DefaultExportFormat NVARCHAR(20) DEFAULT 'csv',
            ChartPreference NVARCHAR(20) DEFAULT 'bar',
            DashboardPreference NVARCHAR(50) DEFAULT 'standard',
            UpdatedAt DATETIME2 NOT NULL DEFAULT GETDATE(),
            CONSTRAINT FK_UserSettings_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );
    END;

    IF OBJECT_ID('EmailVerificationTokens', 'U') IS NULL
    BEGIN
        CREATE TABLE EmailVerificationTokens (
            TokenID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL,
            Token NVARCHAR(255) NOT NULL UNIQUE,
            ExpiresAt DATETIME2 NOT NULL,
            IsUsed BIT NOT NULL DEFAULT 0,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE(),
            CONSTRAINT FK_EmailTokens_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );
    END;

    IF OBJECT_ID('PasswordResetTokens', 'U') IS NULL
    BEGIN
        CREATE TABLE PasswordResetTokens (
            TokenID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL,
            Token NVARCHAR(255) NOT NULL UNIQUE,
            ExpiresAt DATETIME2 NOT NULL,
            IsUsed BIT NOT NULL DEFAULT 0,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE(),
            CONSTRAINT FK_ResetTokens_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );
    END;

    IF OBJECT_ID('LoginHistory', 'U') IS NULL
    BEGIN
        CREATE TABLE LoginHistory (
            LogID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL,
            IPAddress NVARCHAR(100) NULL,
            UserAgent NVARCHAR(500) NULL,
            Browser NVARCHAR(100) NULL,
            OS NVARCHAR(100) NULL,
            Device NVARCHAR(100) NULL,
            Status NVARCHAR(50) NOT NULL,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE()
        );
    END;

    IF OBJECT_ID('UserSessions', 'U') IS NULL
    BEGIN
        CREATE TABLE UserSessions (
            SessionID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL,
            SessionToken NVARCHAR(255) NOT NULL UNIQUE,
            IPAddress NVARCHAR(100) NULL,
            UserAgent NVARCHAR(500) NULL,
            ExpiresAt DATETIME2 NOT NULL,
            IsActive BIT NOT NULL DEFAULT 1,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE()
        );
    END;

    IF OBJECT_ID('AuditLogs', 'U') IS NULL
    BEGIN
        CREATE TABLE AuditLogs (
            LogID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NULL,
            Action NVARCHAR(100) NOT NULL,
            Details NVARCHAR(MAX) NULL,
            IPAddress NVARCHAR(100) NULL,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE()
        );
    END;

    IF OBJECT_ID('Datasets', 'U') IS NULL
    BEGIN
        CREATE TABLE Datasets (
            DatasetID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL,
            DatasetName NVARCHAR(255) NOT NULL,
            OriginalFileName NVARCHAR(255) NOT NULL,
            FileType NVARCHAR(20) NOT NULL,
            TotalRows INT DEFAULT 0,
            TotalColumns INT DEFAULT 0,
            StorageSizeKB FLOAT DEFAULT 0.0,
            IsFavorite BIT DEFAULT 0,
            Tags NVARCHAR(500) NULL,
            LastOpenedAt DATETIME2 NULL,
            UploadDate DATETIME2 NOT NULL DEFAULT GETDATE()
        );
    END;

    IF OBJECT_ID('QueryLogs', 'U') IS NULL
    BEGIN
        CREATE TABLE QueryLogs (
            QueryID INT IDENTITY(1,1) PRIMARY KEY,
            DatasetID INT NULL,
            UserQuestion NVARCHAR(MAX) NOT NULL,
            GeneratedSQL NVARCHAR(MAX) NOT NULL,
            ExecutionStatus NVARCHAR(50) NOT NULL,
            RowsReturned INT DEFAULT 0,
            ExecutionTimeMS FLOAT DEFAULT 0.0,
            ConfidenceScore FLOAT DEFAULT 1.0,
            RetryCount INT DEFAULT 0,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE()
        );
    END;

    IF OBJECT_ID('AINotifications', 'U') IS NULL
    BEGIN
        CREATE TABLE AINotifications (
            NotificationID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL,
            Category NVARCHAR(50) NOT NULL,
            Title NVARCHAR(200) NOT NULL,
            Message NVARCHAR(MAX) NOT NULL,
            MetadataJson NVARCHAR(MAX) NULL,
            IsRead BIT DEFAULT 0,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE(),
            CONSTRAINT FK_AINotifications_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );
    END;

    IF OBJECT_ID('Payments', 'U') IS NULL
    BEGIN
        CREATE TABLE Payments (
            PaymentID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL,
            Amount DECIMAL(10,2) NOT NULL DEFAULT 85.00,
            Currency NVARCHAR(10) DEFAULT 'USD',
            PaymentMethod NVARCHAR(50) DEFAULT 'Stripe Credit Card',
            TransactionID NVARCHAR(100) NULL,
            Status NVARCHAR(50) NOT NULL DEFAULT 'Completed',
            PlanName NVARCHAR(100) DEFAULT 'Enterprise Plan ($85/mo)',
            PaymentDate DATETIME2 NOT NULL DEFAULT GETDATE(),
            SubscriptionEndDate DATETIME2 NULL,
            CONSTRAINT FK_Payments_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('EmailVerificationTokens') AND name = 'Attempts')
    BEGIN
        ALTER TABLE EmailVerificationTokens ADD Attempts INT NOT NULL DEFAULT 0;
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('EmailVerificationTokens') AND name = 'UsedAt')
    BEGIN
        ALTER TABLE EmailVerificationTokens ADD UsedAt DATETIME2 NULL;
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('PasswordResetTokens') AND name = 'Attempts')
    BEGIN
        ALTER TABLE PasswordResetTokens ADD Attempts INT NOT NULL DEFAULT 0;
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('PasswordResetTokens') AND name = 'UsedAt')
    BEGIN
        ALTER TABLE PasswordResetTokens ADD UsedAt DATETIME2 NULL;
    END;

    -- CREATE HIGH PERFORMANCE NON-CLUSTERED INDEXES
    IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Datasets_UserID_UploadDate' AND object_id = OBJECT_ID('Datasets'))
    BEGIN
        CREATE NONCLUSTERED INDEX IX_Datasets_UserID_UploadDate ON Datasets(UserID, UploadDate DESC);
    END;

    IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Datasets_DatasetName' AND object_id = OBJECT_ID('Datasets'))
    BEGIN
        CREATE NONCLUSTERED INDEX IX_Datasets_DatasetName ON Datasets(DatasetName);
    END;

    IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Users_Email' AND object_id = OBJECT_ID('Users'))
    BEGIN
        CREATE NONCLUSTERED INDEX IX_Users_Email ON Users(Email);
    END;

    IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_QueryLogs_DatasetID' AND object_id = OBJECT_ID('QueryLogs'))
    BEGIN
        CREATE NONCLUSTERED INDEX IX_QueryLogs_DatasetID ON QueryLogs(DatasetID, CreatedAt DESC);
    END;

    IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_AINotifications_UserID_CreatedAt' AND object_id = OBJECT_ID('AINotifications'))
    BEGIN
        CREATE NONCLUSTERED INDEX IX_AINotifications_UserID_CreatedAt ON AINotifications(UserID, CreatedAt DESC);
    END;

    IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_UserSessions_Token_Active' AND object_id = OBJECT_ID('UserSessions'))
    BEGIN
        CREATE NONCLUSTERED INDEX IX_UserSessions_Token_Active ON UserSessions(SessionToken, IsActive);
    END;
    """
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(query)
    except Exception as err:
        print("Init DB Notice:", err)

    validate_smtp_config()

    try:
        import threading
        from utils.encryption_migration import migrate_and_encrypt_existing_tables
        threading.Thread(target=migrate_and_encrypt_existing_tables, daemon=True).start()
    except Exception as mig_err:
        print("Encryption Migration Thread Notice:", mig_err)


def validate_smtp_config():
    """
    Validate SMTP environment variables on application startup.
    Logs a developer-friendly notice if credentials are not configured without crashing.
    """
    user = (os.getenv("SMTP_USERNAME") or os.getenv("SMTP_USER") or "").strip()
    pwd = (os.getenv("SMTP_PASSWORD") or "").strip()
    host = (os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER") or "smtp.gmail.com").strip()

    if user and pwd:
        print(f"[SMTP SERVICE] Configured: {host} (User: {user})")
    else:
        print("[WARNING] SMTP credentials incomplete in .env. Configure SMTP_USERNAME and SMTP_PASSWORD for real Gmail inbox delivery.")


def seed_super_admin():
    """
    Seed default Super Admin account and sample enterprise payment records.
    """
    admin_email = "admin@aidataanalystpro.com"
    admin_pass = "AdminPassword123!"

    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("SELECT UserID, Role FROM Users WHERE Email = ?", (admin_email,))
            existing = cursor.fetchone()

            admin_user_id = None
            if not existing:
                import bcrypt
                salt = bcrypt.gensalt(rounds=12)
                pwd_hash = bcrypt.hashpw(admin_pass.encode("utf-8"), salt).decode("utf-8")

                cursor.execute("""
                    INSERT INTO Users (FirstName, LastName, Username, FullName, Email, PasswordHash, IsActive, IsVerified, Role)
                    VALUES ('Super', 'Admin', 'superadmin', 'System Super Admin', ?, ?, 1, 1, 'SuperAdmin')
                """, (admin_email, pwd_hash))
                cursor.execute("SELECT UserID FROM Users WHERE Email = ?", (admin_email,))
                row = cursor.fetchone()
                if row:
                    admin_user_id = row[0]
                print(f"[SUPER ADMIN] Created seed account: {admin_email}")
            else:
                admin_user_id = existing[0]

            # Seed sample payments if Payments table is empty
            cursor.execute("SELECT COUNT(*) FROM Payments")
            p_count = cursor.fetchone()[0]
            if p_count == 0 and admin_user_id:
                cursor.execute("""
                    INSERT INTO Payments (UserID, Amount, Currency, PaymentMethod, TransactionID, Status, PlanName, PaymentDate)
                    VALUES (?, 85.00, 'USD', 'Stripe Credit Card', 'TXN_998124819', 'Completed', 'Enterprise Plan ($85/mo)', GETDATE())
                """, (admin_user_id,))
                cursor.execute("""
                    INSERT INTO Payments (UserID, Amount, Currency, PaymentMethod, TransactionID, Status, PlanName, PaymentDate)
                    VALUES (?, 85.00, 'USD', 'Bank Wire Transfer', 'TXN_998124820', 'Pending', 'Enterprise Plan ($85/mo)', GETDATE())
                """, (admin_user_id,))
                cursor.execute("""
                    INSERT INTO Payments (UserID, Amount, Currency, PaymentMethod, TransactionID, Status, PlanName, PaymentDate)
                    VALUES (?, 85.00, 'USD', 'PayPal Express', 'TXN_998124821', 'Completed', 'Enterprise Plan ($85/mo)', GETDATE())
                """, (admin_user_id,))
                print("[SUPER ADMIN] Initialized sample enterprise payments records.")
    except Exception as err:
        print("Seed Super Admin Notice:", err)


# Auto-run table initialization on module load
try:
    init_db()
    seed_super_admin()
except Exception:
    pass


def is_safe_identifier(identifier: str) -> bool:
    """
    Sanitize table or column names to prevent SQL injection.
    Allows alphanumeric characters and underscores only.
    """
    if not identifier or not isinstance(identifier, str):
        return False
    return bool(re.match(r"^[A-Za-z0-9_]+$", identifier.strip()))


def sanitize_identifier(identifier: str) -> str:
    """
    Clean and enclose identifier in SQL Server brackets [].
    """
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(identifier).strip())
    return f"[{cleaned}]"


import warnings

def run_query(query: str, params: tuple = None) -> pd.DataFrame:
    """
    Execute SELECT SQL Query safely and return Pandas DataFrame.
    Suppresses Pandas read_sql DBAPI UserWarning for optimal execution speed.
    """
    conn = get_connection()
    df = pd.DataFrame()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            if isinstance(conn, SQLiteConnectionAdapter):
                clean_q = re.sub(r"TOP\s+(\d+)", r"LIMIT \1", str(query), flags=re.IGNORECASE)
                if params:
                    df = pd.read_sql(clean_q, conn.conn, params=params)
                else:
                    df = pd.read_sql(clean_q, conn.conn)
            else:
                if params:
                    df = pd.read_sql(query, conn, params=params)
                else:
                    df = pd.read_sql(query, conn)
        try:
            from utils.encryption import decrypt_dataframe
            df = decrypt_dataframe(df)
        except Exception:
            pass
        return df
    finally:
        conn.close()


def get_latest_table(user_id=None) -> str:
    """
    Return latest uploaded dataset table name for the specific logged-in user.
    """
    if user_id:
        try:
            with get_db_cursor() as cursor:
                cursor.execute("SELECT TOP 1 DatasetName FROM Datasets WHERE UserID = ? ORDER BY UploadDate DESC", (user_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    return row[0]
        except Exception:
            pass

    conn = get_connection()
    try:
        if isinstance(conn, SQLiteConnectionAdapter):
            with get_db_cursor() as cursor:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = cursor.fetchall()
        else:
            try:
                from database.queries import get_table_names
            except ModuleNotFoundError:
                from queries import get_table_names
            with get_db_cursor() as cursor:
                cursor.execute(get_table_names())
                tables = cursor.fetchall()

        ignore = {
            "Users", "UserProfiles", "UserSettings", "EmailVerificationTokens",
            "PasswordResetTokens", "LoginHistory", "UserSessions", "AuditLogs",
            "Datasets", "QueryLogs", "sysdiagrams", "sqlite_sequence"
        }
        csv_tables = [table[0] for table in tables if table[0] not in ignore]

        if not csv_tables:
            return None

        return csv_tables[-1]
    finally:
        conn.close()


def get_table_columns(table_name: str) -> list:
    """
    Return list of column names for a given table.
    """
    if not is_safe_identifier(table_name):
        return []

    conn = get_connection()
    try:
        if isinstance(conn, SQLiteConnectionAdapter):
            with get_db_cursor() as cursor:
                cursor.execute(f"PRAGMA table_info({sanitize_identifier(table_name)})")
                cols = cursor.fetchall()
                return [c[1] for c in cols] if cols else []
        else:
            try:
                from database.queries import get_columns
            except ModuleNotFoundError:
                from queries import get_columns
            with get_db_cursor() as cursor:
                cursor.execute(get_columns(), (table_name,))
                columns = cursor.fetchall()
                return [col[0] for col in columns]
    except Exception as e:
        print(f"Error fetching columns for [{table_name}]:", e)
        return []
    finally:
        conn.close()


def get_table_preview(table_name: str, limit: int = 100, offset: int = 0) -> pd.DataFrame:
    """
    Get preview rows of a table using OFFSET ... FETCH NEXT or LIMIT OFFSET for sub-second pagination.
    """
    if not is_safe_identifier(table_name):
        return pd.DataFrame()

    safe_limit = max(1, min(int(limit), 1000))
    safe_offset = max(0, int(offset))

    conn = get_connection()
    try:
        if isinstance(conn, SQLiteConnectionAdapter):
            query = f"SELECT * FROM {sanitize_identifier(table_name)} LIMIT {safe_limit} OFFSET {safe_offset}"
        else:
            query = f"SELECT * FROM {sanitize_identifier(table_name)} ORDER BY (SELECT NULL) OFFSET {safe_offset} ROWS FETCH NEXT {safe_limit} ROWS ONLY"
        return run_query(query)
    except Exception as e:
        print(f"Error previewing table [{table_name}]:", e)
        return pd.DataFrame()
    finally:
        conn.close()