"""
Database Query Registry for AI Data Analyst Pro
Centralized parameterized SQL statements for Data & Enterprise Authentication.
Enforces strict per-user data isolation (UserID).
"""

from database.connection import sanitize_identifier, is_safe_identifier


# ==========================================
# AUTHENTICATION & USER QUERIES
# ==========================================

def get_all_users():
    return """
    SELECT UserID, FirstName, LastName, Username, FullName, Email, PhoneNumber, Country, City, IsActive, IsVerified, Role, CreatedAt
    FROM Users
    ORDER BY CreatedAt DESC
    """


def get_user_by_email():
    return """
    SELECT UserID, FirstName, LastName, Username, FullName, Email, PhoneNumber, Country, City, PasswordHash, ProfileImage, IsActive, IsVerified, Role, FailedLoginAttempts, LockoutUntil, CreatedAt
    FROM Users
    WHERE Email = ?
    """


def get_user_by_username():
    return """
    SELECT UserID, FirstName, LastName, Username, FullName, Email, PhoneNumber, Country, City, PasswordHash, ProfileImage, IsActive, IsVerified, Role, FailedLoginAttempts, LockoutUntil, CreatedAt
    FROM Users
    WHERE Username = ?
    """


def get_user_by_id():
    return """
    SELECT UserID, FirstName, LastName, Username, FullName, Email, PhoneNumber, Country, City, PasswordHash, ProfileImage, IsActive, IsVerified, Role, FailedLoginAttempts, LockoutUntil, CreatedAt
    FROM Users
    WHERE UserID = ?
    """


def create_user():
    return """
    INSERT INTO Users (FirstName, LastName, Username, FullName, Email, PhoneNumber, Country, City, PasswordHash, IsActive, IsVerified, Role, CreatedAt)
    OUTPUT INSERTED.UserID
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 'Analyst', GETDATE())
    """

insert_user = create_user


def update_user_password():
    return """
    UPDATE Users
    SET PasswordHash = ?, FailedLoginAttempts = 0, LockoutUntil = NULL, UpdatedAt = GETDATE()
    WHERE UserID = ?
    """


def update_user_profile_info():
    return """
    UPDATE Users
    SET FirstName = ?, LastName = ?, FullName = ?, Username = ?, Email = ?, PhoneNumber = ?, Country = ?, City = ?, UpdatedAt = GETDATE()
    WHERE UserID = ?
    """


def update_user_avatar():
    return """
    UPDATE Users
    SET ProfileImage = ?, UpdatedAt = GETDATE()
    WHERE UserID = ?
    """


def verify_user_email():
    return """
    UPDATE Users
    SET IsVerified = 1, IsActive = 1, UpdatedAt = GETDATE()
    WHERE UserID = ?
    """


def increment_failed_login():
    return """
    UPDATE Users
    SET FailedLoginAttempts = ISNULL(FailedLoginAttempts, 0) + 1,
        LockoutUntil = CASE WHEN ISNULL(FailedLoginAttempts, 0) + 1 >= 5 THEN DATEADD(MINUTE, 15, GETDATE()) ELSE LockoutUntil END
    WHERE UserID = ?
    """


def reset_failed_login():
    return """
    UPDATE Users
    SET FailedLoginAttempts = 0, LockoutUntil = NULL
    WHERE UserID = ?
    """


# ==========================================
# USER PROFILE & SETTINGS QUERIES
# ==========================================

def get_user_profile():
    return """
    SELECT ProfileID, UserID, Bio, Occupation, Company, Department, Designation, Website, LinkedIn, GitHub, Portfolio, ProfileImage, Timezone, Language
    FROM UserProfiles
    WHERE UserID = ?
    """


def upsert_user_profile():
    return """
    IF EXISTS (SELECT 1 FROM UserProfiles WHERE UserID = ?)
    BEGIN
        UPDATE UserProfiles
        SET Bio = ?, Occupation = ?, Company = ?, Department = ?, Designation = ?, Website = ?, LinkedIn = ?, GitHub = ?, Portfolio = ?, Timezone = ?, Language = ?, UpdatedAt = GETDATE()
        WHERE UserID = ?
    END
    ELSE
    BEGIN
        INSERT INTO UserProfiles (UserID, Bio, Occupation, Company, Department, Designation, Website, LinkedIn, GitHub, Portfolio, Timezone, Language, UpdatedAt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE())
    END
    """


def get_user_settings():
    return """
    SELECT SettingID, UserID, Theme, DateFormat, DefaultExportFormat, ChartPreference, DashboardPreference
    FROM UserSettings
    WHERE UserID = ?
    """


def upsert_user_settings():
    return """
    IF EXISTS (SELECT 1 FROM UserSettings WHERE UserID = ?)
    BEGIN
        UPDATE UserSettings
        SET Theme = ?, DateFormat = ?, DefaultExportFormat = ?, ChartPreference = ?, DashboardPreference = ?, UpdatedAt = GETDATE()
        WHERE UserID = ?
    END
    ELSE
    BEGIN
        INSERT INTO UserSettings (UserID, Theme, DateFormat, DefaultExportFormat, ChartPreference, DashboardPreference, UpdatedAt)
        VALUES (?, ?, ?, ?, ?, ?, GETDATE())
    END
    """


# ==========================================
# TOKENS (VERIFICATION & RESET)
# ==========================================

def create_email_verification_token():
    return """
    INSERT INTO EmailVerificationTokens (UserID, Token, ExpiresAt, IsUsed, CreatedAt)
    VALUES (?, ?, DATEADD(MINUTE, 10, GETDATE()), 0, GETDATE())
    """


def get_email_verification_token():
    return """
    SELECT TokenID, UserID, Token, ExpiresAt, IsUsed, ISNULL(Attempts, 0) AS Attempts
    FROM EmailVerificationTokens
    WHERE Token = ? AND IsUsed = 0 AND ExpiresAt > GETDATE()
    """


def increment_email_token_attempt():
    return """
    UPDATE EmailVerificationTokens
    SET Attempts = ISNULL(Attempts, 0) + 1
    WHERE Token = ?
    """


def mark_email_token_used():
    return """
    UPDATE EmailVerificationTokens
    SET IsUsed = 1, UsedAt = GETDATE()
    WHERE Token = ?
    """


def create_password_reset_token():
    return """
    INSERT INTO PasswordResetTokens (UserID, Token, ExpiresAt, IsUsed, CreatedAt)
    VALUES (?, ?, DATEADD(MINUTE, 10, GETDATE()), 0, GETDATE())
    """


def get_password_reset_token():
    return """
    SELECT TokenID, UserID, Token, ExpiresAt, IsUsed, ISNULL(Attempts, 0) AS Attempts
    FROM PasswordResetTokens
    WHERE Token = ? AND IsUsed = 0 AND ExpiresAt > GETDATE()
    """


def increment_reset_token_attempt():
    return """
    UPDATE PasswordResetTokens
    SET Attempts = ISNULL(Attempts, 0) + 1
    WHERE Token = ?
    """


def mark_reset_token_used():
    return """
    UPDATE PasswordResetTokens
    SET IsUsed = 1, UsedAt = GETDATE()
    WHERE Token = ?
    """


def check_resend_rate_limit():
    return """
    SELECT COUNT(*)
    FROM EmailVerificationTokens
    WHERE UserID = ? AND CreatedAt >= DATEADD(MINUTE, -15, GETDATE())
    """


# ==========================================
# LOGIN HISTORY & ACTIVE SESSIONS
# ==========================================

def log_login_event():
    return """
    INSERT INTO LoginHistory (UserID, IPAddress, UserAgent, Browser, OS, Device, Status, CreatedAt)
    VALUES (?, ?, ?, ?, ?, ?, ?, GETDATE())
    """


def get_user_login_history(limit=10):
    return f"""
    SELECT TOP ({limit}) LogID, IPAddress, UserAgent, Browser, OS, Device, Status, CreatedAt
    FROM LoginHistory
    WHERE UserID = ?
    ORDER BY CreatedAt DESC
    """


def create_user_session():
    return """
    INSERT INTO UserSessions (UserID, SessionToken, IPAddress, UserAgent, ExpiresAt, IsActive, CreatedAt)
    VALUES (?, ?, DATEADD(DAY, 7, GETDATE()), 1, GETDATE())
    """


def invalidate_user_session():
    return """
    UPDATE UserSessions
    SET IsActive = 0
    WHERE SessionToken = ?
    """


def invalidate_all_other_sessions():
    return """
    UPDATE UserSessions
    SET IsActive = 0
    WHERE UserID = ? AND SessionToken <> ?
    """


def get_active_sessions_for_user():
    return """
    SELECT SessionID, SessionToken, IPAddress, UserAgent, ExpiresAt, CreatedAt
    FROM UserSessions
    WHERE UserID = ? AND IsActive = 1 AND ExpiresAt > GETDATE()
    ORDER BY CreatedAt DESC
    """


# ==========================================
# AUDIT LOGS
# ==========================================

def insert_audit_log():
    return """
    INSERT INTO AuditLogs (UserID, Action, Details, IPAddress, CreatedAt)
    VALUES (?, ?, ?, ?, GETDATE())
    """


def get_user_audit_logs(limit=15):
    return f"""
    SELECT TOP ({limit}) LogID, Action, Details, IPAddress, CreatedAt
    FROM AuditLogs
    WHERE UserID = ?
    ORDER BY CreatedAt DESC
    """


# ==========================================
# DATASET QUERIES (ISOLATED PER USER ID)
# ==========================================

def get_all_datasets(user_id=None, sort_by="UploadDate", order="DESC", date_from=None, date_to=None):
    valid_cols = {"UploadDate": "UploadDate", "DatasetName": "DatasetName", "TotalRows": "TotalRows", "StorageSizeKB": "StorageSizeKB"}
    col = valid_cols.get(sort_by, "UploadDate")
    ord_str = "ASC" if str(order).upper() == "ASC" else "DESC"

    where_clauses = []
    if user_id:
        where_clauses.append(f"UserID = {int(user_id)}")
    if date_from:
        where_clauses.append("UploadDate >= ?")
    if date_to:
        where_clauses.append("UploadDate <= ?")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    return f"""
    SELECT
        DatasetID,
        DatasetName,
        OriginalFileName,
        TotalRows,
        TotalColumns,
        UploadDate,
        StorageSizeKB,
        ISNULL(IsFavorite, 0) AS IsFavorite,
        Tags,
        LastOpenedAt
    FROM Datasets
    {where_sql}
    ORDER BY {col} {ord_str}
    """


def get_recently_opened_datasets(user_id=None, limit=5):
    where_sql = f"WHERE UserID = {int(user_id)} AND LastOpenedAt IS NOT NULL" if user_id else "WHERE LastOpenedAt IS NOT NULL"
    return f"""
    SELECT TOP ({limit})
        DatasetID,
        DatasetName,
        OriginalFileName,
        TotalRows,
        TotalColumns,
        UploadDate,
        StorageSizeKB,
        ISNULL(IsFavorite, 0) AS IsFavorite,
        Tags,
        LastOpenedAt
    FROM Datasets
    {where_sql}
    ORDER BY LastOpenedAt DESC
    """


def get_top_dataset_by_rows(user_id=None):
    where_sql = f"WHERE UserID = {int(user_id)}" if user_id else ""
    return f"""
    SELECT TOP 1 DatasetName, TotalRows, OriginalFileName
    FROM Datasets
    {where_sql}
    ORDER BY TotalRows DESC
    """


def get_largest_dataset_by_size(user_id=None):
    where_sql = f"WHERE UserID = {int(user_id)}" if user_id else ""
    return f"""
    SELECT TOP 1 DatasetName, StorageSizeKB, OriginalFileName
    FROM Datasets
    {where_sql}
    ORDER BY StorageSizeKB DESC
    """


def get_most_asked_queries(user_id=None, limit=5):
    where_sql = f"WHERE DatasetID IN (SELECT DatasetID FROM Datasets WHERE UserID = {int(user_id)})" if user_id else ""
    return f"""
    SELECT TOP ({limit}) UserQuestion, COUNT(*) AS Frequency, MAX(CreatedAt) AS LastAsked
    FROM QueryLogs
    {where_sql}
    GROUP BY UserQuestion
    ORDER BY Frequency DESC
    """


def search_datasets(user_id=None, sort_by="UploadDate", order="DESC", date_from=None, date_to=None):
    valid_cols = {"UploadDate": "UploadDate", "DatasetName": "DatasetName", "TotalRows": "TotalRows", "StorageSizeKB": "StorageSizeKB"}
    col = valid_cols.get(sort_by, "UploadDate")
    ord_str = "ASC" if str(order).upper() == "ASC" else "DESC"

    where_clauses = ["(DatasetName LIKE ? OR OriginalFileName LIKE ? OR Tags LIKE ?)"]
    if user_id:
        where_clauses.append(f"UserID = {int(user_id)}")
    if date_from:
        where_clauses.append("UploadDate >= ?")
    if date_to:
        where_clauses.append("UploadDate <= ?")

    where_sql = "WHERE " + " AND ".join(where_clauses)

    return f"""
    SELECT
        DatasetID,
        DatasetName,
        OriginalFileName,
        TotalRows,
        TotalColumns,
        UploadDate,
        StorageSizeKB,
        ISNULL(IsFavorite, 0) AS IsFavorite,
        Tags,
        LastOpenedAt
    FROM Datasets
    {where_sql}
    ORDER BY {col} {ord_str}
    """


def toggle_favorite_dataset(user_id=None):
    if user_id:
        return f"UPDATE Datasets SET IsFavorite = CASE WHEN ISNULL(IsFavorite, 0) = 1 THEN 0 ELSE 1 END WHERE DatasetID = ? AND UserID = {int(user_id)}"
    return "UPDATE Datasets SET IsFavorite = CASE WHEN ISNULL(IsFavorite, 0) = 1 THEN 0 ELSE 1 END WHERE DatasetID = ?"


def update_dataset_tags(user_id=None):
    if user_id:
        return f"UPDATE Datasets SET Tags = ? WHERE DatasetID = ? AND UserID = {int(user_id)}"
    return "UPDATE Datasets SET Tags = ? WHERE DatasetID = ?"


def touch_dataset_opened_at(user_id=None):
    if user_id:
        return f"UPDATE Datasets SET LastOpenedAt = GETDATE() WHERE DatasetID = ? AND UserID = {int(user_id)}"
    return "UPDATE Datasets SET LastOpenedAt = GETDATE() WHERE DatasetID = ?"


def insert_dataset():
    return """
    INSERT INTO Datasets (UserID, DatasetName, OriginalFileName, FileType, TotalRows, TotalColumns, StorageSizeKB, IsFavorite, Tags, LastOpenedAt)
    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, GETDATE())
    """


def get_table_names():
    return """
    SELECT TABLE_NAME
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_TYPE='BASE TABLE'
    """


def get_columns():
    return """
    SELECT COLUMN_NAME
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = ?
    ORDER BY ORDINAL_POSITION
    """


def get_total_datasets(user_id=None):
    if user_id:
        return f"SELECT COUNT(*) FROM Datasets WHERE UserID = {int(user_id)}"
    return "SELECT COUNT(*) FROM Datasets"


def get_total_rows(user_id=None):
    if user_id:
        return f"SELECT ISNULL(SUM(TotalRows), 0) FROM Datasets WHERE UserID = {int(user_id)}"
    return "SELECT ISNULL(SUM(TotalRows), 0) FROM Datasets"


def get_total_queries(user_id=None):
    if user_id:
        return f"SELECT COUNT(*) FROM QueryLogs WHERE DatasetID IN (SELECT DatasetID FROM Datasets WHERE UserID = {int(user_id)})"
    return "SELECT COUNT(*) FROM QueryLogs"


def get_total_charts_generated(user_id=None):
    if user_id:
        return f"SELECT COUNT(*) FROM QueryLogs WHERE DatasetID IN (SELECT DatasetID FROM Datasets WHERE UserID = {int(user_id)}) AND ExecutionStatus = 'Success'"
    return "SELECT COUNT(*) FROM QueryLogs WHERE ExecutionStatus = 'Success'"


def get_latest_dataset(user_id=None):
    where_sql = f"WHERE UserID = {int(user_id)}" if user_id else ""
    return f"""
    SELECT TOP 1
        DatasetName,
        OriginalFileName,
        TotalRows,
        TotalColumns,
        UploadDate,
        DatasetID
    FROM Datasets
    {where_sql}
    ORDER BY UploadDate DESC
    """


def delete_dataset_record(user_id=None):
    if user_id:
        return f"DELETE FROM Datasets WHERE DatasetID = ? AND UserID = {int(user_id)}"
    return "DELETE FROM Datasets WHERE DatasetID = ?"


def delete_dataset_table(table_name: str) -> str:
    if not is_safe_identifier(table_name):
        raise ValueError(f"Invalid table name for deletion: {table_name}")
    return f"DROP TABLE {sanitize_identifier(table_name)}"


def get_dataset_by_id(user_id=None):
    if user_id:
        return f"SELECT DatasetID, UserID, DatasetName, OriginalFileName, FileType, TotalRows, TotalColumns, UploadDate, StorageSizeKB, ISNULL(IsFavorite,0) AS IsFavorite, Tags, LastOpenedAt FROM Datasets WHERE DatasetID = ? AND UserID = {int(user_id)}"
    return "SELECT DatasetID, UserID, DatasetName, OriginalFileName, FileType, TotalRows, TotalColumns, UploadDate, StorageSizeKB, ISNULL(IsFavorite,0) AS IsFavorite, Tags, LastOpenedAt FROM Datasets WHERE DatasetID = ?"


def get_dataset_table_name(user_id=None):
    if user_id:
        return f"SELECT DatasetName FROM Datasets WHERE DatasetID = ? AND UserID = {int(user_id)}"
    return "SELECT DatasetName FROM Datasets WHERE DatasetID = ?"


def update_dataset_name(user_id=None):
    if user_id:
        return f"UPDATE Datasets SET DatasetName = ? WHERE DatasetID = ? AND UserID = {int(user_id)}"
    return "UPDATE Datasets SET DatasetName = ?"


def log_query_execution():
    return """
    INSERT INTO QueryLogs
    (DatasetID, UserQuestion, GeneratedSQL, ExecutionStatus, RowsReturned, ExecutionTimeMS, ConfidenceScore, RetryCount)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """


# ==========================================
# AI NOTIFICATIONS & SMART ASSISTANT QUERIES
# ==========================================

def create_ai_notification():
    return """
    INSERT INTO AINotifications (UserID, Category, Title, Message, MetadataJson, IsRead, CreatedAt)
    VALUES (?, ?, ?, ?, ?, 0, GETDATE())
    """


def get_user_ai_notifications(limit=30):
    return f"""
    SELECT TOP ({int(limit)}) NotificationID, UserID, Category, Title, Message, MetadataJson, IsRead, CreatedAt
    FROM AINotifications
    WHERE UserID = ?
    ORDER BY CreatedAt DESC
    """


def get_unread_ai_notification_count():
    return """
    SELECT COUNT(*)
    FROM AINotifications
    WHERE UserID = ? AND IsRead = 0
    """


def mark_ai_notification_read():
    return """
    UPDATE AINotifications
    SET IsRead = 1
    WHERE UserID = ? AND (NotificationID = ? OR ? = 0)
    """


def clear_user_ai_notifications():
    return """
    DELETE FROM AINotifications
    WHERE UserID = ?
    """


# ==========================================
# SUPER ADMIN QUERY HELPERS
# ==========================================
def get_admin_kpis():
    return """
    SELECT
        (SELECT COUNT(*) FROM Users) AS TotalUsers,
        (SELECT COUNT(*) FROM Users WHERE IsActive = 1) AS ActiveUsers,
        (SELECT COUNT(*) FROM Users WHERE IsActive = 0) AS SuspendedUsers,
        (SELECT ISNULL(SUM(Amount), 0) FROM Payments WHERE Status = 'Completed') AS TotalRevenue,
        (SELECT COUNT(*) FROM Payments WHERE Status = 'Completed') AS CompletedPayments,
        (SELECT COUNT(*) FROM Payments WHERE Status IN ('Pending', 'Pending_Review')) AS PendingPayments,
        (SELECT COUNT(*) FROM Datasets) AS TotalDatasets,
        (SELECT COUNT(*) FROM Users WHERE SubscriptionStatus = 'expiring_soon' AND Role NOT IN ('SuperAdmin', 'Admin')) AS ExpiringSoonUsers,
        (SELECT COUNT(*) FROM Users WHERE SubscriptionStatus = 'expired' AND Role NOT IN ('SuperAdmin', 'Admin')) AS ExpiredUsers
    """


def get_site_announcement():
    return "SELECT TOP 1 Title, Message, IsEnabled FROM SiteAnnouncement ORDER BY AnnouncementID DESC"


def upsert_site_announcement():
    return """
    UPDATE SiteAnnouncement SET Title = ?, Message = ?, IsEnabled = ?, UpdatedAt = GETDATE()
    """


def insert_site_announcement():
    return """
    INSERT INTO SiteAnnouncement (Title, Message, IsEnabled, UpdatedAt)
    VALUES (?, ?, ?, GETDATE())
    """


def get_per_user_storage_admin():
    return """
    SELECT U.UserID, U.FullName, U.Email,
           COUNT(D.DatasetID) AS DatasetCount,
           ISNULL(SUM(D.StorageSizeKB), 0) AS TotalStorageKB,
           ISNULL(SUM(D.TotalRows), 0) AS TotalRows
    FROM Users U
    LEFT JOIN Datasets D ON D.UserID = U.UserID
    WHERE U.Role NOT IN ('SuperAdmin', 'Admin')
    GROUP BY U.UserID, U.FullName, U.Email
    HAVING COUNT(D.DatasetID) > 0
    ORDER BY TotalStorageKB DESC
    """


def get_monthly_revenue_trend():
    """
    Takes (start_date, end_date) as bind params - the Super Admin dashboard lets the
    date range be picked freely instead of a fixed lookback window.
    """
    return """
    SELECT FORMAT(PaymentDate, 'yyyy-MM') AS MonthLabel, SUM(Amount) AS Revenue
    FROM Payments
    WHERE Status = 'Completed' AND PaymentDate >= ? AND PaymentDate < DATEADD(day, 1, ?)
    GROUP BY FORMAT(PaymentDate, 'yyyy-MM')
    ORDER BY MonthLabel ASC
    """


def get_monthly_signups_trend():
    """
    Takes (start_date, end_date) as bind params - see get_monthly_revenue_trend().
    """
    return """
    SELECT FORMAT(CreatedAt, 'yyyy-MM') AS MonthLabel, COUNT(*) AS NewUsers
    FROM Users
    WHERE CreatedAt >= ? AND CreatedAt < DATEADD(day, 1, ?)
    GROUP BY FORMAT(CreatedAt, 'yyyy-MM')
    ORDER BY MonthLabel ASC
    """


def get_expiring_users_admin(limit=10):
    return f"""
    SELECT TOP {int(limit)} UserID, FullName, Email, SubscriptionStatus, SubscriptionEndDate
    FROM Users
    WHERE SubscriptionStatus IN ('expiring_soon', 'expired')
      AND Role NOT IN ('SuperAdmin', 'Admin')
    ORDER BY SubscriptionEndDate ASC
    """


def get_all_users_admin():
    return """
    SELECT U.UserID, U.FirstName, U.LastName, U.Username, U.FullName, U.Email, U.PhoneNumber, U.Country, U.City, U.ProfileImage, U.IsActive, U.IsVerified, U.Role, U.CreatedAt,
           ISNULL((SELECT TOP 1 IPAddress FROM AuditLogs WHERE UserID = U.UserID AND IPAddress IS NOT NULL ORDER BY CreatedAt DESC), '127.0.0.1') AS LastIPAddress,
           ISNULL(U.SubscriptionStatus, 'active') AS SubscriptionStatus,
           U.SubscriptionEndDate
    FROM Users U
    ORDER BY 
        CASE 
            WHEN U.IsActive = 0 OR U.SubscriptionStatus = 'suspended' THEN 1
            WHEN U.SubscriptionStatus = 'expired' THEN 2
            WHEN U.SubscriptionStatus = 'expiring_soon' THEN 3
            ELSE 4
        END ASC,
        U.CreatedAt DESC
    """


def update_user_status_admin():
    return """
    UPDATE Users
    SET IsActive = ?
    WHERE UserID = ?
    """


def update_user_role_admin():
    return """
    UPDATE Users
    SET Role = ?
    WHERE UserID = ?
    """


def delete_user_admin():
    return """
    DELETE FROM Users
    WHERE UserID = ?
    """


def get_all_payments_admin(order_by="DESC"):
    sort_dir = "ASC" if str(order_by).upper() == "ASC" else "DESC"
    return f"""
    SELECT P.PaymentID, P.UserID, U.FullName, U.Email, P.Amount, P.Currency, P.PaymentMethod, P.TransactionID, P.Status, P.PlanName, P.PaymentDate, P.ScreenshotPath,
           P.PaymentType, P.RejectionReason, P.ReviewedBy, P.ReviewedAt, U.PhoneNumber
    FROM Payments P
    LEFT JOIN Users U ON P.UserID = U.UserID
    ORDER BY P.PaymentDate {sort_dir}
    """


def update_payment_status_admin():
    return """
    UPDATE Payments
    SET Status = ?
    WHERE PaymentID = ?
    """


def approve_payment_admin():
    return """
    UPDATE Payments
    SET Status = 'Completed', ReviewedBy = ?, ReviewedAt = GETDATE(), RejectionReason = NULL
    WHERE PaymentID = ?
    """


def reject_payment_admin():
    return """
    UPDATE Payments
    SET Status = 'Rejected', RejectionReason = ?, ReviewedBy = ?, ReviewedAt = GETDATE()
    WHERE PaymentID = ?
    """


def get_payment_by_id():
    return """
    SELECT P.PaymentID, P.UserID, P.Amount, P.TransactionID, P.PlanName, P.PaymentType, P.Status,
           U.FullName, U.Email, U.SubscriptionStatus, U.SubscriptionEndDate
    FROM Payments P
    LEFT JOIN Users U ON P.UserID = U.UserID
    WHERE P.PaymentID = ?
    """


def check_duplicate_transaction_id():
    return "SELECT COUNT(*) FROM Payments WHERE TransactionID = ? AND Status != 'Rejected'"


def check_user_has_pending_payment():
    return "SELECT COUNT(*) FROM Payments WHERE UserID = ? AND Status IN ('Pending', 'Pending_Review')"


def insert_payment_full():
    return """
    INSERT INTO Payments (UserID, Amount, Currency, PaymentMethod, TransactionID, Status, PlanName, PaymentType, ScreenshotPath, PaymentDate)
    OUTPUT INSERTED.PaymentID
    VALUES (?, ?, 'PKR', ?, ?, 'Pending', ?, ?, ?, GETDATE())
    """


# ==========================================
# PAYMENT-FIRST REGISTRATION & SUBSCRIPTION LIFECYCLE
# ==========================================

def get_payment_settings():
    return "SELECT TOP 1 PlanName, PlanPriceLabel, AccountTitle, BankName, AccountNumber, IBAN, JazzCashNumber, EasypaisaNumber, SadaPayNumber, Instructions FROM PaymentSettings ORDER BY SettingID DESC"


def update_payment_settings():
    return """
    UPDATE PaymentSettings
    SET PlanName = ?, PlanPriceLabel = ?, AccountTitle = ?, BankName = ?, AccountNumber = ?, IBAN = ?, JazzCashNumber = ?, EasypaisaNumber = ?, SadaPayNumber = ?, Instructions = ?, UpdatedAt = GETDATE()
    """


def set_user_pending_approval():
    return """
    UPDATE Users
    SET SubscriptionStatus = 'pending_approval', SubscriptionEndDate = NULL, SuspensionReason = NULL
    WHERE UserID = ?
    """


def activate_user_new_subscription():
    """
    Fresh calendar-month cycle starting from the approval moment (used for new
    registrations and for reactivating an already-suspended/rejected user).
    SQL Server's DATEADD(month, ...) clamps automatically to the last valid day of the
    target month (e.g. 31 Jan + 1 month -> 28/29 Feb), which is exactly the "one
    calendar month" rule this feature needs.
    """
    return """
    UPDATE Users
    SET SubscriptionStatus = 'active',
        SubscriptionEndDate = DATEADD(month, 1, CAST(GETDATE() AS DATE)),
        SuspensionReason = NULL,
        IsActive = 1
    WHERE UserID = ?
    """


def extend_user_subscription_one_month():
    """
    Used when a user renews BEFORE their current period has expired - extends from
    their existing end date rather than from today, so no remaining days are lost.
    """
    return """
    UPDATE Users
    SET SubscriptionStatus = 'active',
        SubscriptionEndDate = DATEADD(month, 1, CAST(SubscriptionEndDate AS DATE)),
        SuspensionReason = NULL,
        IsActive = 1
    WHERE UserID = ?
    """


def set_user_rejected():
    return """
    UPDATE Users
    SET SubscriptionStatus = 'rejected', SuspensionReason = ?
    WHERE UserID = ?
    """


def auto_suspend_overdue_users():
    """
    Bulk sweep: any non-admin user whose subscription end date has passed and who is
    still marked 'active' gets suspended. Runs as one statement so it's cheap even at
    scale and safe to run repeatedly (idempotent - already-suspended rows are excluded).
    """
    return """
    UPDATE Users
    SET SubscriptionStatus = 'suspended', SuspensionReason = 'Subscription period expired without a renewed payment.'
    OUTPUT INSERTED.UserID, INSERTED.FullName, INSERTED.Email
    WHERE SubscriptionStatus = 'active'
      AND SubscriptionEndDate IS NOT NULL
      AND SubscriptionEndDate < GETDATE()
      AND Role NOT IN ('SuperAdmin', 'Admin')
    """


def get_users_due_on_date():
    """
    Users whose SubscriptionEndDate falls on the given date (still 'active', not yet
    suspended). The target date is computed in Python using Asia/Karachi time and
    passed in as a bind parameter, so reminder timing is correct regardless of what
    timezone the database server itself happens to be running in.
    """
    return """
    SELECT UserID, FullName, Email, SubscriptionEndDate
    FROM Users
    WHERE SubscriptionStatus = 'active'
      AND Role NOT IN ('SuperAdmin', 'Admin')
      AND CAST(SubscriptionEndDate AS DATE) = ?
    """


def has_reminder_been_sent():
    return "SELECT COUNT(*) FROM ReminderLog WHERE UserID = ? AND ReminderType = ? AND ForDueDate = ?"


def log_reminder_sent():
    return "INSERT INTO ReminderLog (UserID, ReminderType, ForDueDate) VALUES (?, ?, ?)"


def get_all_audit_logs_admin(limit=100):
    return f"""
    SELECT TOP ({int(limit)}) LogID, UserID, Action, Details, IPAddress, CreatedAt
    FROM AuditLogs
    ORDER BY CreatedAt DESC
    """


# ==========================================
# QUERY HISTORY QUERIES (USER DATA ISOLATION)
# ==========================================

def insert_query_history():
    return """
    INSERT INTO QueryHistory (UserID, DatasetID, UserQuestion, GeneratedSQL, ExecutionStatus, RowsReturned, ExecutionTimeMS, ChartType, ErrorMessage, CreatedAt)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE())
    """


def get_user_query_history(limit=50):
    return f"""
    SELECT TOP ({int(limit)}) HistoryID, DatasetID, UserQuestion, GeneratedSQL, ExecutionStatus, RowsReturned, ExecutionTimeMS, ChartType, ErrorMessage, CreatedAt
    FROM QueryHistory
    WHERE UserID = ?
    ORDER BY CreatedAt DESC
    """


def delete_query_history_item():
    return """
    DELETE FROM QueryHistory
    WHERE HistoryID = ? AND UserID = ?
    """


def clear_user_query_history():
    return """
    DELETE FROM QueryHistory
    WHERE UserID = ?
    """


def get_user_query_count_since():
    return """
    SELECT COUNT(*) FROM QueryHistory
    WHERE UserID = ? AND CreatedAt >= ?
    """


# ==========================================
# SCHEDULED REPORTS QUERIES
# ==========================================

def create_scheduled_report():
    return """
    INSERT INTO ScheduledReports (UserID, DatasetID, ReportType, Frequency, RecipientEmail, ScheduleTime, IsEnabled, CreatedAt)
    VALUES (?, ?, ?, ?, ?, ?, 1, GETDATE())
    """


def get_user_scheduled_reports():
    return """
    SELECT R.ReportID, R.DatasetID, D.DatasetName, R.ReportType, R.Frequency, R.RecipientEmail, R.ScheduleTime, R.IsEnabled, R.LastRunAt, R.CreatedAt
    FROM ScheduledReports R
    LEFT JOIN Datasets D ON R.DatasetID = D.DatasetID
    WHERE R.UserID = ?
    ORDER BY R.CreatedAt DESC
    """


def toggle_scheduled_report():
    return """
    UPDATE ScheduledReports
    SET IsEnabled = CASE WHEN IsEnabled = 1 THEN 0 ELSE 1 END
    WHERE ReportID = ? AND UserID = ?
    """


def delete_scheduled_report():
    return """
    DELETE FROM ScheduledReports
    WHERE ReportID = ? AND UserID = ?
    """


def get_due_scheduled_reports():
    # The extra "D.UserID = R.UserID" is a defense-in-depth invariant, not just a join
    # condition - it stops a report row from ever being processed against a dataset it
    # doesn't actually belong to, even if one somehow got created that way.
    return """
    SELECT R.ReportID, R.UserID, R.DatasetID, D.OriginalFileName, D.DatasetName, R.ReportType, R.Frequency, R.RecipientEmail, R.ScheduleTime, R.LastRunAt
    FROM ScheduledReports R
    JOIN Datasets D ON R.DatasetID = D.DatasetID AND D.UserID = R.UserID
    WHERE R.IsEnabled = 1
    """


def update_scheduled_report_last_run():
    return """
    UPDATE ScheduledReports
    SET LastRunAt = GETDATE()
    WHERE ReportID = ?
    """


# ==========================================
# ALERT RULES & HISTORY QUERIES
# ==========================================

def create_alert_rule():
    return """
    INSERT INTO AlertRules (UserID, DatasetID, MetricName, ConditionOperator, ThresholdValue, RecipientEmail, IsEnabled, CreatedAt)
    VALUES (?, ?, ?, ?, ?, ?, 1, GETDATE())
    """


def get_user_alert_rules():
    return """
    SELECT A.AlertID, A.DatasetID, D.DatasetName, A.MetricName, A.ConditionOperator, A.ThresholdValue, A.RecipientEmail, A.IsEnabled, A.LastTriggeredAt, A.CreatedAt
    FROM AlertRules A
    LEFT JOIN Datasets D ON A.DatasetID = D.DatasetID
    WHERE A.UserID = ?
    ORDER BY A.CreatedAt DESC
    """


def toggle_alert_rule():
    return """
    UPDATE AlertRules
    SET IsEnabled = CASE WHEN IsEnabled = 1 THEN 0 ELSE 1 END
    WHERE AlertID = ? AND UserID = ?
    """


def delete_alert_rule():
    return """
    DELETE FROM AlertRules
    WHERE AlertID = ? AND UserID = ?
    """


def get_active_alert_rules():
    # The extra "D.UserID = A.UserID" is a defense-in-depth invariant, not just a join
    # condition - it stops an alert rule from ever being evaluated against a dataset it
    # doesn't actually belong to, even if one somehow got created that way.
    return """
    SELECT A.AlertID, A.UserID, A.DatasetID, D.OriginalFileName, D.DatasetName, A.MetricName, A.ConditionOperator, A.ThresholdValue, A.RecipientEmail, A.LastTriggeredAt
    FROM AlertRules A
    JOIN Datasets D ON A.DatasetID = D.DatasetID AND D.UserID = A.UserID
    WHERE A.IsEnabled = 1
    """


def update_alert_rule_last_triggered():
    return """
    UPDATE AlertRules
    SET LastTriggeredAt = GETDATE()
    WHERE AlertID = ?
    """


def record_alert_trigger():
    return """
    INSERT INTO AlertHistory (AlertID, UserID, TriggeredValue, Message, TriggeredAt)
    VALUES (?, ?, ?, ?, GETDATE())
    """


def get_user_alert_history(limit=50):
    return f"""
    SELECT TOP ({int(limit)}) H.HistoryID, H.AlertID, H.TriggeredValue, H.Message, H.TriggeredAt, A.MetricName, D.DatasetName
    FROM AlertHistory H
    LEFT JOIN AlertRules A ON H.AlertID = A.AlertID
    LEFT JOIN Datasets D ON A.DatasetID = D.DatasetID
    WHERE H.UserID = ?
    ORDER BY H.TriggeredAt DESC
    """


# ==========================================
# PUBLIC CONTACT FORM QUERIES
# ==========================================

def insert_contact_message():
    return """
    INSERT INTO ContactMessages (FullName, Email, Subject, Message, Status, IPAddress, CreatedAt)
    VALUES (?, ?, ?, ?, 'New', ?, GETDATE())
    """


def get_all_contact_messages():
    return """
    SELECT MessageID, FullName, Email, Subject, Message, Status, IPAddress, CreatedAt, AdminReply, RepliedAt
    FROM ContactMessages
    ORDER BY CreatedAt DESC
    """


def update_contact_message_status():
    return """
    UPDATE ContactMessages
    SET Status = ?
    WHERE MessageID = ?
    """


def save_contact_message_reply():
    return """
    UPDATE ContactMessages
    SET AdminReply = ?, RepliedAt = GETDATE(), Status = 'Responded'
    WHERE MessageID = ?
    """


def get_contact_message_by_id():
    return """
    SELECT MessageID, FullName, Email, Subject, Message
    FROM ContactMessages
    WHERE MessageID = ?
    """


def delete_contact_message():
    return """
    DELETE FROM ContactMessages
    WHERE MessageID = ?
    """