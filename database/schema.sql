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