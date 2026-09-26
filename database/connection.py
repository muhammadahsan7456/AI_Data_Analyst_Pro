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
    # Reuse physical ODBC connections across get_connection() calls instead of a fresh
    # TCP/login handshake per request. This is pyodbc's default already, set explicitly
    # here so it's documented and doesn't depend on the driver's default staying True.
    pyodbc.pooling = True
except ImportError:
    pyodbc = None


def adapt_tsql_for_sqlite(query: str) -> str:
    """
    Safely transform Microsoft T-SQL syntax to SQLite syntax.
    Handles DATEADD, GETDATE(), ISNULL(), and SELECT TOP N -> LIMIT N without syntax errors.
    """
    if not query or not isinstance(query, str):
        return query

    s = query.strip()

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

    # Transform SELECT TOP N ... -> SELECT ... LIMIT N
    top_match = re.search(r"\bSELECT\s+TOP\s+\(?(\d+)\)?\s+", s, flags=re.IGNORECASE)
    if top_match:
        limit_val = top_match.group(1)
        # Remove SELECT TOP N
        s = re.sub(r"\bSELECT\s+TOP\s+\(?\d+\)?\s+", "SELECT ", s, count=1, flags=re.IGNORECASE).strip()
        # Append LIMIT N before trailing semicolon or at end
        if not re.search(r"\bLIMIT\s+\d+\b", s, re.IGNORECASE):
            if s.endswith(";"):
                s = s[:-1].strip() + f" LIMIT {limit_val};"
            else:
                s = s + f" LIMIT {limit_val}"

    return s


class SQLiteCursorAdapter:
    def __init__(self, cursor):
        self._cursor = cursor
        self.last_inserted_id = None

    def execute(self, sql, params=()):
        s = str(sql)

        s = adapt_tsql_for_sqlite(s)
        self._cursor.execute(s, params or ())
        if self._cursor.lastrowid:
            self.last_inserted_id = self._cursor.lastrowid
        return self

    def executemany(self, sql, seq_of_parameters=()):
        s = adapt_tsql_for_sqlite(str(sql))
        self._cursor.executemany(s, seq_of_parameters or ())
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
        try:
            self.conn.execute("PRAGMA synchronous = OFF;")
            self.conn.execute("PRAGMA journal_mode = WAL;")
            self.conn.execute("PRAGMA temp_store = MEMORY;")
        except Exception:
            pass

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
    server = os.getenv("DB_SERVER", "localhost")
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
    Establish and return a SQL Server database connection.
    A local SQLite fallback is available ONLY when ALLOW_SQLITE_FALLBACK=true is set in
    .env, for local development/testing convenience. By default (including production),
    a SQL Server outage raises a real error instead of silently switching to SQLite and
    serving different (and likely empty) data without anyone noticing.
    """
    allow_fallback = os.getenv("ALLOW_SQLITE_FALLBACK", "false").strip().lower() == "true"
    last_err = None

    if pyodbc is not None:
        try:
            conn_str = get_connection_string()
            return pyodbc.connect(conn_str, timeout=3)
        except Exception as primary_err:
            # No hardcoded secondary server to retry against - a specific developer
            # machine's hostname doesn't mean anything on anyone else's setup, and
            # silently trying another server on failure contradicts this function's
            # own documented behavior (raise a real error, don't fail over quietly).
            last_err = primary_err

    if not allow_fallback:
        print(f"[DATABASE ERROR] Could not connect to SQL Server ({type(last_err).__name__ if last_err else 'pyodbc unavailable'}).")
        raise ConnectionError(
            "Could not connect to the SQL Server database. Set ALLOW_SQLITE_FALLBACK=true "
            "in .env only if you intend to run against a local SQLite database for "
            "development/testing."
        ) from last_err

    # Local development/testing fallback only - never used unless explicitly enabled.
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
        )''',
        '''CREATE TABLE IF NOT EXISTS Payments (
            PaymentID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            Amount REAL NOT NULL DEFAULT 25000.00,
            Currency TEXT DEFAULT 'PKR',
            PaymentMethod TEXT DEFAULT 'Easypaisa',
            TransactionID TEXT NULL,
            Status TEXT NOT NULL DEFAULT 'Pending',
            PlanName TEXT DEFAULT 'Enterprise Plan (PKR 25,000/mo)',
            ScreenshotPath TEXT NULL,
            PaymentDate TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        )''',
        '''CREATE TABLE IF NOT EXISTS NewsletterSubscribers (
            SubscriberID INTEGER PRIMARY KEY AUTOINCREMENT,
            Email TEXT NOT NULL UNIQUE,
            Status TEXT NOT NULL DEFAULT 'Active',
            IPAddress TEXT NULL,
            SubscribedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS ContactMessages (
            MessageID INTEGER PRIMARY KEY AUTOINCREMENT,
            FullName TEXT NOT NULL,
            Email TEXT NOT NULL,
            Subject TEXT NOT NULL DEFAULT 'General Inquiry',
            Message TEXT NOT NULL,
            Status TEXT NOT NULL DEFAULT 'New',
            IPAddress TEXT NULL,
            AdminReply TEXT NULL,
            RepliedAt TEXT NULL,
            CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS QueryHistory (
            HistoryID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            DatasetID INTEGER NULL,
            UserQuestion TEXT NOT NULL,
            GeneratedSQL TEXT NOT NULL,
            ExecutionStatus TEXT NOT NULL DEFAULT 'Success',
            RowsReturned INTEGER DEFAULT 0,
            ExecutionTimeMS REAL DEFAULT 0.0,
            ChartType TEXT DEFAULT 'auto',
            ErrorMessage TEXT NULL,
            CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS ScheduledReports (
            ReportID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            DatasetID INTEGER NOT NULL,
            ReportType TEXT NOT NULL DEFAULT 'Executive Summary',
            Frequency TEXT NOT NULL DEFAULT 'Daily',
            RecipientEmail TEXT NOT NULL,
            ScheduleTime TEXT DEFAULT '09:00',
            IsEnabled INTEGER NOT NULL DEFAULT 1,
            LastRunAt TEXT NULL,
            CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS AlertRules (
            AlertID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            DatasetID INTEGER NOT NULL,
            MetricName TEXT NOT NULL,
            ConditionOperator TEXT NOT NULL DEFAULT '>',
            ThresholdValue REAL NOT NULL,
            RecipientEmail TEXT NOT NULL,
            IsEnabled INTEGER NOT NULL DEFAULT 1,
            LastTriggeredAt TEXT NULL,
            CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS AlertHistory (
            HistoryID INTEGER PRIMARY KEY AUTOINCREMENT,
            AlertID INTEGER NOT NULL,
            UserID INTEGER NOT NULL,
            TriggeredValue REAL NOT NULL,
            Message TEXT NOT NULL,
            TriggeredAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )'''
    ]

    for t in tables:
        try:
            cursor.execute(t)
        except Exception as e:
            print("SQLite Table Creation Notice:", e)

    try:
        cursor.execute("PRAGMA table_info(Payments)")
        p_cols = [r[1] for r in cursor.fetchall()]
        if "ScreenshotPath" not in p_cols:
            cursor.execute("ALTER TABLE Payments ADD COLUMN ScreenshotPath TEXT NULL")
    except Exception:
        pass

    conn.commit()


_db_initialized = False

def init_db():
    """
    Automatically create missing core system tables, enterprise auth columns, and non-clustered performance indexes.
    """
    global _db_initialized
    if _db_initialized:
        return
    _db_initialized = True

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
            Amount DECIMAL(10,2) NOT NULL DEFAULT 25000.00,
            Currency NVARCHAR(10) DEFAULT 'PKR',
            PaymentMethod NVARCHAR(50) DEFAULT 'Easypaisa',
            TransactionID NVARCHAR(100) NULL,
            Status NVARCHAR(50) NOT NULL DEFAULT 'Completed',
            PlanName NVARCHAR(100) DEFAULT 'Enterprise Plan (PKR 25,000/mo)',
            PaymentDate DATETIME2 NOT NULL DEFAULT GETDATE(),
            SubscriptionEndDate DATETIME2 NULL,
            CONSTRAINT FK_Payments_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );
    END;

    IF OBJECT_ID('NewsletterSubscribers', 'U') IS NULL
    BEGIN
        CREATE TABLE NewsletterSubscribers (
            SubscriberID INT IDENTITY(1,1) PRIMARY KEY,
            Email NVARCHAR(255) NOT NULL UNIQUE,
            Status NVARCHAR(50) NOT NULL DEFAULT 'Active',
            IPAddress NVARCHAR(100) NULL,
            SubscribedAt DATETIME2 NOT NULL DEFAULT GETDATE()
        );
    END;

    IF OBJECT_ID('ContactMessages', 'U') IS NULL
    BEGIN
        CREATE TABLE ContactMessages (
            MessageID INT IDENTITY(1,1) PRIMARY KEY,
            FullName NVARCHAR(200) NOT NULL,
            Email NVARCHAR(255) NOT NULL,
            Subject NVARCHAR(200) NOT NULL DEFAULT 'General Inquiry',
            Message NVARCHAR(MAX) NOT NULL,
            Status NVARCHAR(50) NOT NULL DEFAULT 'New',
            IPAddress NVARCHAR(100) NULL,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE()
        );
    END;

    IF OBJECT_ID('QueryHistory', 'U') IS NULL
    BEGIN
        CREATE TABLE QueryHistory (
            HistoryID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL,
            DatasetID INT NULL,
            UserQuestion NVARCHAR(MAX) NOT NULL,
            GeneratedSQL NVARCHAR(MAX) NOT NULL,
            ExecutionStatus NVARCHAR(50) NOT NULL DEFAULT 'Success',
            RowsReturned INT DEFAULT 0,
            ExecutionTimeMS FLOAT DEFAULT 0.0,
            ChartType NVARCHAR(50) DEFAULT 'auto',
            ErrorMessage NVARCHAR(MAX) NULL,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE(),
            CONSTRAINT FK_QueryHistory_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );
    END;

    IF OBJECT_ID('SiteAnnouncement', 'U') IS NULL
    BEGIN
        CREATE TABLE SiteAnnouncement (
            AnnouncementID INT IDENTITY(1,1) PRIMARY KEY,
            Title NVARCHAR(200) NOT NULL DEFAULT 'Platform Announcement',
            Message NVARCHAR(MAX) NOT NULL DEFAULT '',
            IsEnabled BIT NOT NULL DEFAULT 0,
            UpdatedAt DATETIME2 NOT NULL DEFAULT GETDATE()
        );
    END;

    IF OBJECT_ID('ScheduledReports', 'U') IS NULL
    BEGIN
        CREATE TABLE ScheduledReports (
            ReportID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL,
            DatasetID INT NOT NULL,
            ReportType NVARCHAR(100) NOT NULL DEFAULT 'Executive Summary',
            Frequency NVARCHAR(50) NOT NULL DEFAULT 'Daily',
            RecipientEmail NVARCHAR(255) NOT NULL,
            ScheduleTime NVARCHAR(20) DEFAULT '09:00',
            IsEnabled BIT NOT NULL DEFAULT 1,
            LastRunAt DATETIME2 NULL,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE(),
            CONSTRAINT FK_ScheduledReports_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );
    END;

    IF OBJECT_ID('AlertRules', 'U') IS NULL
    BEGIN
        CREATE TABLE AlertRules (
            AlertID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL,
            DatasetID INT NOT NULL,
            MetricName NVARCHAR(200) NOT NULL,
            ConditionOperator NVARCHAR(10) NOT NULL DEFAULT '>',
            ThresholdValue FLOAT NOT NULL,
            RecipientEmail NVARCHAR(255) NOT NULL,
            IsEnabled BIT NOT NULL DEFAULT 1,
            LastTriggeredAt DATETIME2 NULL,
            CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE(),
            CONSTRAINT FK_AlertRules_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );
    END;

    IF OBJECT_ID('AlertHistory', 'U') IS NULL
    BEGIN
        CREATE TABLE AlertHistory (
            HistoryID INT IDENTITY(1,1) PRIMARY KEY,
            AlertID INT NOT NULL,
            UserID INT NOT NULL,
            TriggeredValue FLOAT NOT NULL,
            Message NVARCHAR(MAX) NOT NULL,
            TriggeredAt DATETIME2 NOT NULL DEFAULT GETDATE(),
            CONSTRAINT FK_AlertHistory_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
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

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('Payments') AND name = 'ScreenshotPath')
    BEGIN
        ALTER TABLE Payments ADD ScreenshotPath NVARCHAR(500) NULL;
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('ContactMessages') AND name = 'AdminReply')
    BEGIN
        ALTER TABLE ContactMessages ADD AdminReply NVARCHAR(MAX) NULL;
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('ContactMessages') AND name = 'RepliedAt')
    BEGIN
        ALTER TABLE ContactMessages ADD RepliedAt DATETIME2 NULL;
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('Users') AND name = 'SubscriptionStatus')
    BEGIN
        ALTER TABLE Users ADD SubscriptionStatus NVARCHAR(50) DEFAULT 'active';
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('Users') AND name = 'SubscriptionEndDate')
    BEGIN
        ALTER TABLE Users ADD SubscriptionEndDate DATETIME2 NULL;
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('Users') AND name = 'SuspendedAt')
    BEGIN
        ALTER TABLE Users ADD SuspendedAt DATETIME2 NULL;
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('Users') AND name = 'SuspensionReason')
    BEGIN
        ALTER TABLE Users ADD SuspensionReason NVARCHAR(500) NULL;
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('Payments') AND name = 'PaymentType')
    BEGIN
        ALTER TABLE Payments ADD PaymentType NVARCHAR(20) NOT NULL DEFAULT 'New';
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('Payments') AND name = 'RejectionReason')
    BEGIN
        ALTER TABLE Payments ADD RejectionReason NVARCHAR(500) NULL;
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('Payments') AND name = 'ReviewedBy')
    BEGIN
        ALTER TABLE Payments ADD ReviewedBy INT NULL;
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('Payments') AND name = 'ReviewedAt')
    BEGIN
        ALTER TABLE Payments ADD ReviewedAt DATETIME2 NULL;
    END;

    IF OBJECT_ID('PaymentSettings', 'U') IS NULL
    BEGIN
        CREATE TABLE PaymentSettings (
            SettingID INT IDENTITY(1,1) PRIMARY KEY,
            PlanName NVARCHAR(200) NOT NULL DEFAULT 'Enterprise Plan',
            PlanPriceLabel NVARCHAR(100) NOT NULL DEFAULT 'PKR 25,000 / month',
            AccountTitle NVARCHAR(200) NOT NULL DEFAULT '',
            BankName NVARCHAR(200) NULL,
            AccountNumber NVARCHAR(100) NULL,
            IBAN NVARCHAR(100) NULL,
            JazzCashNumber NVARCHAR(50) NULL,
            EasypaisaNumber NVARCHAR(50) NULL,
            SadaPayNumber NVARCHAR(50) NULL,
            Instructions NVARCHAR(MAX) NULL,
            UpdatedAt DATETIME2 NOT NULL DEFAULT GETDATE()
        );
    END;

    IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('PaymentSettings') AND name = 'SadaPayNumber')
    BEGIN
        ALTER TABLE PaymentSettings ADD SadaPayNumber NVARCHAR(50) NULL;
    END;

    IF OBJECT_ID('ReminderLog', 'U') IS NULL
    BEGIN
        CREATE TABLE ReminderLog (
            LogID INT IDENTITY(1,1) PRIMARY KEY,
            UserID INT NOT NULL,
            ReminderType NVARCHAR(20) NOT NULL,
            ForDueDate DATE NOT NULL,
            SentAt DATETIME2 NOT NULL DEFAULT GETDATE(),
            CONSTRAINT UQ_ReminderLog_User_Type_Date UNIQUE (UserID, ReminderType, ForDueDate),
            CONSTRAINT FK_ReminderLog_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );
    END;

    IF NOT EXISTS (SELECT * FROM PaymentSettings)
    BEGIN
        INSERT INTO PaymentSettings (PlanName, PlanPriceLabel, AccountTitle, BankName, AccountNumber, IBAN, JazzCashNumber, EasypaisaNumber, Instructions)
        VALUES (
            'Enterprise Plan', 'PKR 25,000 / month', 'AI Data Analyst Pro',
            'Meezan Bank', '03053107456', NULL, '03053107456', '03053107456',
            'Pay the amount to the account below, take a screenshot of the payment, and upload it in the form.'
        );
    END;

    -- Initialize NULL subscription dates & statuses
    UPDATE Users SET SubscriptionEndDate = DATEADD(day, 30, ISNULL(CreatedAt, GETDATE())) WHERE SubscriptionEndDate IS NULL;
    UPDATE Users SET SubscriptionStatus = 'active' WHERE SubscriptionStatus IS NULL;

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

    IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_QueryHistory_UserID_CreatedAt' AND object_id = OBJECT_ID('QueryHistory'))
    BEGIN
        CREATE NONCLUSTERED INDEX IX_QueryHistory_UserID_CreatedAt ON QueryHistory(UserID, CreatedAt DESC);
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

    # Table value encryption disabled to ensure 100% C-speed SQL filtering and query compatibility for Ask AI
    pass


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
                clean_q = adapt_tsql_for_sqlite(str(query))
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

    safe_limit = max(1, min(int(limit), 1000000))
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