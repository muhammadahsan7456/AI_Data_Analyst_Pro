-- ==========================================
-- AI Data Analyst Pro
-- Database Creation & Schema Script
-- ==========================================

IF NOT EXISTS (SELECT * FROM sys.databases WHERE name = 'AI_Data_Analyst_Pro')
BEGIN
    CREATE DATABASE AI_Data_Analyst_Pro;
END
GO

USE AI_Data_Analyst_Pro;
GO

-- ==========================================
-- Users Table
-- ==========================================

IF OBJECT_ID('Users', 'U') IS NULL
BEGIN
    CREATE TABLE Users
    (
        UserID INT IDENTITY(1,1) PRIMARY KEY,
        FullName NVARCHAR(100) NOT NULL,
        Email NVARCHAR(255) NOT NULL UNIQUE,
        PasswordHash NVARCHAR(500) NOT NULL,
        ProfileImage NVARCHAR(500) NULL,
        IsActive BIT NOT NULL DEFAULT 1,
        CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE(),
        UpdatedAt DATETIME2 NULL
    );
END
GO

-- Insert default user if not existing
IF NOT EXISTS (SELECT * FROM Users WHERE Email = 'ahsan@example.com')
BEGIN
    INSERT INTO Users (FullName, Email, PasswordHash, ProfileImage)
    VALUES ('Muhammad Ahsan', 'ahsan@example.com', '123456789', NULL);
END
GO

-- ==========================================
-- Datasets Table
-- ==========================================

IF OBJECT_ID('Datasets', 'U') IS NULL
BEGIN
    CREATE TABLE Datasets
    (
        DatasetID INT IDENTITY(1,1) PRIMARY KEY,
        UserID INT NOT NULL,
        DatasetName NVARCHAR(255) NOT NULL,
        OriginalFileName NVARCHAR(255) NOT NULL,
        FileType NVARCHAR(20) NOT NULL,
        TotalRows INT DEFAULT 0,
        TotalColumns INT DEFAULT 0,
        StorageSizeKB FLOAT DEFAULT 0.0,
        UploadDate DATETIME2 NOT NULL DEFAULT GETDATE(),
        CONSTRAINT FK_Datasets_Users
            FOREIGN KEY (UserID)
            REFERENCES Users(UserID)
            ON DELETE CASCADE
    );
END
GO

-- ==========================================
-- Query Logs Table
-- ==========================================

IF OBJECT_ID('QueryLogs', 'U') IS NULL
BEGIN
    CREATE TABLE QueryLogs
    (
        QueryID INT IDENTITY(1,1) PRIMARY KEY,
        DatasetID INT NULL,
        UserQuestion NVARCHAR(MAX) NOT NULL,
        GeneratedSQL NVARCHAR(MAX) NOT NULL,
        ExecutionStatus NVARCHAR(50) NOT NULL, -- 'Success', 'Error'
        RowsReturned INT DEFAULT 0,
        ExecutionTimeMS FLOAT DEFAULT 0.0,
        ConfidenceScore FLOAT DEFAULT 1.0,
        RetryCount INT DEFAULT 0,
        CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE()
    );
END
GO

-- ==========================================
-- Query History Table (User Isolation)
-- ==========================================

IF OBJECT_ID('QueryHistory', 'U') IS NULL
BEGIN
    CREATE TABLE QueryHistory
    (
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
END
GO

-- ==========================================
-- Scheduled Reports Table
-- ==========================================

IF OBJECT_ID('ScheduledReports', 'U') IS NULL
BEGIN
    CREATE TABLE ScheduledReports
    (
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
END
GO

-- ==========================================
-- Alert Rules & History Tables
-- ==========================================

IF OBJECT_ID('AlertRules', 'U') IS NULL
BEGIN
    CREATE TABLE AlertRules
    (
        AlertID INT IDENTITY(1,1) PRIMARY KEY,
        UserID INT NOT NULL,
        DatasetID INT NOT NULL,
        MetricName NVARCHAR(100) NOT NULL,
        ConditionOperator NVARCHAR(20) NOT NULL DEFAULT '>',
        ThresholdValue FLOAT NOT NULL,
        RecipientEmail NVARCHAR(255) NOT NULL,
        IsEnabled BIT NOT NULL DEFAULT 1,
        LastTriggeredAt DATETIME2 NULL,
        CreatedAt DATETIME2 NOT NULL DEFAULT GETDATE(),
        CONSTRAINT FK_AlertRules_Users FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
    );
END
GO

IF OBJECT_ID('AlertHistory', 'U') IS NULL
BEGIN
    CREATE TABLE AlertHistory
    (
        HistoryID INT IDENTITY(1,1) PRIMARY KEY,
        AlertID INT NOT NULL,
        UserID INT NOT NULL,
        TriggeredValue FLOAT NOT NULL,
        Message NVARCHAR(MAX) NOT NULL,
        TriggeredAt DATETIME2 NOT NULL DEFAULT GETDATE()
    );
END
GO