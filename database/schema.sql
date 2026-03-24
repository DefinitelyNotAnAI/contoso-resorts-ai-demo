-- =============================================================================
-- Contoso Resorts AI — Database Schema
-- Target: Fabric SQL Database (also compatible with Azure SQL Database)
-- =============================================================================
-- Run this script against the database to create all tables.
-- Supports idempotent execution (DROP IF EXISTS + CREATE).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Drop tables in FK-safe order
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS dbo.ServiceRequests;
GO
DROP TABLE IF EXISTS dbo.Surveys;
GO
DROP TABLE IF EXISTS dbo.Inventory;
GO
DROP TABLE IF EXISTS dbo.Experiences;
GO
DROP TABLE IF EXISTS dbo.Bookings;
GO
DROP TABLE IF EXISTS dbo.Guests;
GO
DROP TABLE IF EXISTS dbo.Properties;
GO

CREATE TABLE dbo.Properties (
    PropertyID   NVARCHAR(10)   NOT NULL PRIMARY KEY,
    Name         NVARCHAR(100)  NOT NULL,
    ShortName    NVARCHAR(50)   NOT NULL,
    Location     NVARCHAR(100)  NOT NULL,
    Description  NVARCHAR(500)  NOT NULL,
    Tier         NVARCHAR(20)   NOT NULL,  -- Luxury, Premium, Select
    RoomCount    INT            NOT NULL
);
GO

-- -----------------------------------------------------------------------------
-- Guests — Chain-wide guest master with loyalty
-- -----------------------------------------------------------------------------
CREATE TABLE dbo.Guests (
    GuestID      NVARCHAR(20)   NOT NULL PRIMARY KEY,
    FirstName    NVARCHAR(50)   NOT NULL,
    LastName     NVARCHAR(50)   NOT NULL,
    Email        NVARCHAR(100)  NOT NULL,
    Phone        NVARCHAR(20)   NULL,
    HomeCity     NVARCHAR(100)  NULL,
    Country      NVARCHAR(50)   NOT NULL DEFAULT 'US',
    LoyaltyTier  NVARCHAR(20)   NOT NULL,  -- Platinum, Gold, Silver, Member
    LoyaltyPoints INT           NOT NULL DEFAULT 0,
    MemberSince  DATE           NOT NULL,
    Preferences  NVARCHAR(MAX)  NULL       -- JSON: room temp, pillow type, dietary, etc.
);
GO

-- -----------------------------------------------------------------------------
-- Bookings — Cross-property stay history
-- -----------------------------------------------------------------------------
CREATE TABLE dbo.Bookings (
    BookingID       NVARCHAR(20)    NOT NULL PRIMARY KEY,
    GuestID         NVARCHAR(20)    NOT NULL,
    PropertyID      NVARCHAR(10)    NOT NULL,
    CheckIn         DATE            NOT NULL,
    CheckOut        DATE            NOT NULL,
    RoomType        NVARCHAR(30)    NOT NULL,  -- Standard, Deluxe, Suite, Family Suite, Penthouse
    RoomNumber      NVARCHAR(10)    NULL,
    RatePerNight    DECIMAL(10, 2)  NOT NULL,
    TotalAmount     DECIMAL(10, 2)  NOT NULL,
    Status          NVARCHAR(20)    NOT NULL,  -- Completed, Upcoming, Cancelled
    SpecialRequests NVARCHAR(500)   NULL,
    BookedDate      DATE            NOT NULL,
    CONSTRAINT FK_Bookings_Guests     FOREIGN KEY (GuestID)    REFERENCES dbo.Guests(GuestID),
    CONSTRAINT FK_Bookings_Properties FOREIGN KEY (PropertyID) REFERENCES dbo.Properties(PropertyID)
);
GO

-- -----------------------------------------------------------------------------
-- Experiences — Per-property offerings (spa, dining, activities)
-- -----------------------------------------------------------------------------
CREATE TABLE dbo.Experiences (
    ExperienceID NVARCHAR(20)    NOT NULL PRIMARY KEY,
    PropertyID   NVARCHAR(10)    NOT NULL,
    Name         NVARCHAR(100)   NOT NULL,
    Category     NVARCHAR(30)    NOT NULL,  -- Spa, Dining, Activity, Kids, Wellness
    Description  NVARCHAR(500)   NOT NULL,
    Price        DECIMAL(10, 2)  NOT NULL,
    Duration     NVARCHAR(20)    NULL,       -- e.g., '90 min'
    Available    BIT             NOT NULL DEFAULT 1,
    CONSTRAINT FK_Experiences_Properties FOREIGN KEY (PropertyID) REFERENCES dbo.Properties(PropertyID)
);
GO

-- -----------------------------------------------------------------------------
-- Inventory — Availability grid for date-change scenario
-- -----------------------------------------------------------------------------
CREATE TABLE dbo.Inventory (
    PropertyID  NVARCHAR(10)  NOT NULL,
    Date        DATE          NOT NULL,
    RoomType    NVARCHAR(30)  NOT NULL,
    TotalRooms  INT           NOT NULL,
    BookedRooms INT           NOT NULL DEFAULT 0,
    Available   INT           NOT NULL,  -- Populated by data gen: TotalRooms - BookedRooms
    CONSTRAINT PK_Inventory PRIMARY KEY (PropertyID, Date, RoomType),
    CONSTRAINT FK_Inventory_Properties FOREIGN KEY (PropertyID) REFERENCES dbo.Properties(PropertyID)
);
GO

-- -----------------------------------------------------------------------------
-- Surveys — Post-stay feedback ("Medallia" data)
-- -----------------------------------------------------------------------------
CREATE TABLE dbo.Surveys (
    SurveyID      NVARCHAR(20)  NOT NULL PRIMARY KEY,
    GuestID       NVARCHAR(20)  NOT NULL,
    BookingID     NVARCHAR(20)  NOT NULL,
    PropertyID    NVARCHAR(10)  NOT NULL,
    OverallRating INT           NOT NULL,  -- 1-10
    NPS           INT           NOT NULL,  -- 0-10
    Cleanliness   INT           NOT NULL,  -- 1-5
    Service       INT           NOT NULL,  -- 1-5
    FoodBeverage  INT           NOT NULL,  -- 1-5
    Spa           INT           NULL,      -- 1-5 (null if not used)
    Activities    INT           NULL,      -- 1-5 (null if not used)
    [FreeText]    NVARCHAR(MAX) NULL,      -- Open-ended feedback
    SubmittedDate DATE          NOT NULL,
    CONSTRAINT FK_Surveys_Guests     FOREIGN KEY (GuestID)    REFERENCES dbo.Guests(GuestID),
    CONSTRAINT FK_Surveys_Bookings   FOREIGN KEY (BookingID)  REFERENCES dbo.Bookings(BookingID),
    CONSTRAINT FK_Surveys_Properties FOREIGN KEY (PropertyID) REFERENCES dbo.Properties(PropertyID)
);
GO

-- -----------------------------------------------------------------------------
-- ServiceRequests — In-stay operations requests (mirrors Amadeus HotSOS export)
-- Epic 4.1: wired into the AI pipeline to surface proactive flags
-- -----------------------------------------------------------------------------
CREATE TABLE dbo.ServiceRequests (
    RequestID        NVARCHAR(20)   NOT NULL PRIMARY KEY,
    GuestID          NVARCHAR(20)   NOT NULL,
    BookingID        NVARCHAR(20)   NULL,        -- stay context (nullable for lobby/phone requests)
    PropertyID       NVARCHAR(10)   NOT NULL,
    RequestedDate    DATETIME2      NOT NULL,
    Department       NVARCHAR(30)   NOT NULL,    -- Housekeeping / Engineering / F&B / Front Desk / Guest Services
    Category         NVARCHAR(50)   NOT NULL,    -- Extra Linens, AC Issue, Noise Complaint, Room Service, etc.
    [Description]    NVARCHAR(500)  NULL,
    Priority         NVARCHAR(10)   NOT NULL,    -- Low / Medium / High / Urgent
    [Status]         NVARCHAR(20)   NOT NULL,    -- Open / InProgress / Completed / Escalated / Cancelled
    AssignedTo       NVARCHAR(100)  NULL,
    CompletedDate    DATETIME2      NULL,
    ResponseMinutes  INT            NULL,         -- time from request to first action — key AI signal
    ResolutionNotes  NVARCHAR(500)  NULL,
    GuestSatisfied   BIT            NULL,         -- NULL = not rated, 1 = satisfied, 0 = unsatisfied
    CONSTRAINT FK_ServiceRequests_Guests     FOREIGN KEY (GuestID)    REFERENCES dbo.Guests(GuestID),
    CONSTRAINT FK_ServiceRequests_Bookings   FOREIGN KEY (BookingID)  REFERENCES dbo.Bookings(BookingID),
    CONSTRAINT FK_ServiceRequests_Properties FOREIGN KEY (PropertyID) REFERENCES dbo.Properties(PropertyID)
);
GO

-- =============================================================================
-- Seed data: Properties
-- =============================================================================
INSERT INTO dbo.Properties (PropertyID, Name, ShortName, Location, Description, Tier, RoomCount) VALUES
('P-001', 'Contoso Park City Resort & Spa',    'Park City',    'Park City, UT',    'Ski-in/ski-out luxury resort with signature spa, après-ski dining, and mountain views. The crown jewel of the Contoso Resorts portfolio.',                    'Luxury',  120),
('P-002', 'Contoso Myrtle Beach Oceanfront',    'Myrtle Beach', 'Myrtle Beach, SC', 'Premium beachfront resort, recently renovated with modern oceanview rooms, water sports center, and award-winning seafood restaurant.',                    'Premium', 280),
('P-003', 'Contoso Orlando Family Resort',      'Orlando',      'Orlando, FL',      'Premium family-focused resort near major theme parks. Features an acclaimed kids club, lazy river pool, and character dining experiences.',               'Premium', 350),
('P-004', 'Contoso New York Grand Central',     'New York',     'New York, NY',     'Boutique urban hotel steps from Grand Central and the Theater District. Rooftop bar with skyline views, curated local experience packages.',              'Select',   85),
('P-005', 'Contoso Gatlinburg Mountain Lodge',  'Gatlinburg',   'Gatlinburg, TN',   'Intimate mountain retreat at the gateway to the Great Smoky Mountains. Hiking concierge, farm-to-table dining, and Southern hospitality charm.',          'Select',   60);
GO

-- =============================================================================
-- Schema verification (INFORMATION_SCHEMA — works on both Fabric SQL and Azure SQL)
-- =============================================================================
SELECT 
    TABLE_NAME AS TableName,
    COUNT(COLUMN_NAME) AS ColumnCount
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'dbo'
GROUP BY TABLE_NAME
ORDER BY TABLE_NAME;
