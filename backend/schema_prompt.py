"""
schema_prompt.py — DDL-injected system prompt for GPT-writes-SQL

The LLM receives the full table schema so it can generate accurate SQL
against the Contoso Resorts database (Fabric SQL or Azure SQL — both use T-SQL).
"""

# ---------------------------------------------------------------------------
# Full DDL — injected verbatim into the LLM system prompt
# This mirrors database/schema.sql (update both if schema changes)
# ---------------------------------------------------------------------------
SCHEMA_DDL = """
-- =============================================================================
-- Contoso Resorts AI — Database Schema (Fabric SQL / Azure SQL)
-- =============================================================================

-- dbo.Properties — The 5 Contoso Resorts locations
CREATE TABLE dbo.Properties (
    PropertyID   NVARCHAR(10)   NOT NULL PRIMARY KEY,
    Name         NVARCHAR(100)  NOT NULL,
    ShortName    NVARCHAR(50)   NOT NULL,
    Location     NVARCHAR(100)  NOT NULL,
    Description  NVARCHAR(500)  NOT NULL,
    Tier         NVARCHAR(20)   NOT NULL   -- Luxury, Premium, Select
);

-- Seed data (all 5 properties):
-- ('P-001', 'Contoso Park City Resort & Spa',   'Park City',    'Park City, UT',    'Tier: Luxury')
-- ('P-002', 'Contoso Myrtle Beach Oceanfront',  'Myrtle Beach', 'Myrtle Beach, SC', 'Tier: Premium')
-- ('P-003', 'Contoso Orlando Family Resort',    'Orlando',      'Orlando, FL',      'Tier: Premium')
-- ('P-004', 'Contoso New York Grand Central',   'New York',     'New York, NY',     'Tier: Select')
-- ('P-005', 'Contoso Gatlinburg Mountain Lodge','Gatlinburg',   'Gatlinburg, TN',   'Tier: Select')

-- dbo.Guests — ~5,000 guests with loyalty data
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

-- Demo persona GuestIDs:
--   Dana Lakehouse  → G-0001001  (Platinum)
--   Anne Thropic    → G-0002001  (Gold)
--   Victor Storr    → G-0003001  (Silver)

-- dbo.Bookings — ~12,000 cross-property stays
CREATE TABLE dbo.Bookings (
    BookingID       NVARCHAR(20)    NOT NULL PRIMARY KEY,
    GuestID         NVARCHAR(20)    NOT NULL,  -- FK → dbo.Guests
    PropertyID      NVARCHAR(10)    NOT NULL,  -- FK → dbo.Properties
    CheckIn         DATE            NOT NULL,
    CheckOut        DATE            NOT NULL,
    RoomType        NVARCHAR(30)    NOT NULL,  -- Standard, Deluxe, Suite, Family Suite, Penthouse
    RoomNumber      NVARCHAR(10)    NULL,
    RatePerNight    DECIMAL(10, 2)  NOT NULL,
    TotalAmount     DECIMAL(10, 2)  NOT NULL,
    Status          NVARCHAR(20)    NOT NULL,  -- Completed, Upcoming, Cancelled
    SpecialRequests NVARCHAR(500)   NULL,
    BookedDate      DATE            NOT NULL
);

-- dbo.Experiences — ~200 per-property offerings
CREATE TABLE dbo.Experiences (
    ExperienceID NVARCHAR(20)    NOT NULL PRIMARY KEY,
    PropertyID   NVARCHAR(10)    NOT NULL,  -- FK → dbo.Properties
    Name         NVARCHAR(100)   NOT NULL,
    Category     NVARCHAR(30)    NOT NULL,  -- Spa, Dining, Activity, Kids, Wellness
    Description  NVARCHAR(500)   NOT NULL,
    Price        DECIMAL(10, 2)  NOT NULL,
    Duration     NVARCHAR(20)    NULL,      -- e.g., '90 min'
    Available    BIT             NOT NULL DEFAULT 1
);

-- dbo.Inventory — 90-day availability grid
CREATE TABLE dbo.Inventory (
    PropertyID  NVARCHAR(10)  NOT NULL,  -- FK → dbo.Properties
    Date        DATE          NOT NULL,
    RoomType    NVARCHAR(30)  NOT NULL,
    TotalRooms  INT           NOT NULL,
    BookedRooms INT           NOT NULL DEFAULT 0,
    Available   INT           NOT NULL  -- TotalRooms - BookedRooms
    -- PK: (PropertyID, Date, RoomType)
);

-- dbo.Surveys — ~4,000 post-stay feedback (simulated Medallia data)
CREATE TABLE dbo.Surveys (
    SurveyID      NVARCHAR(20)  NOT NULL PRIMARY KEY,
    GuestID       NVARCHAR(20)  NOT NULL,  -- FK → dbo.Guests
    BookingID     NVARCHAR(20)  NOT NULL,  -- FK → dbo.Bookings
    PropertyID    NVARCHAR(10)  NOT NULL,  -- FK → dbo.Properties
    OverallRating INT           NOT NULL,  -- 1-10
    NPS           INT           NOT NULL,  -- 0-10
    Cleanliness   INT           NOT NULL,  -- 1-5
    Service       INT           NOT NULL,  -- 1-5
    FoodBeverage  INT           NOT NULL,  -- 1-5
    Spa           INT           NULL,      -- 1-5 (NULL if spa not used)
    Activities    INT           NULL,      -- 1-5 (NULL if no activities)
    [FreeText]    NVARCHAR(MAX) NULL,      -- Open-ended feedback (MUST be bracketed in SQL)
    SubmittedDate DATE          NOT NULL
);

-- dbo.ServiceRequests -- ~500 in-stay operations requests (simulated HotSOS export)
CREATE TABLE dbo.ServiceRequests (
    RequestID       NVARCHAR(20)   NOT NULL PRIMARY KEY,  -- SR-NNNNNNN
    GuestID         NVARCHAR(20)   NOT NULL,  -- FK → dbo.Guests
    BookingID       NVARCHAR(20)   NULL,       -- FK → dbo.Bookings (nullable)
    PropertyID      NVARCHAR(10)   NOT NULL,  -- FK → dbo.Properties
    RequestedDate   DATETIME2      NOT NULL,
    Department      NVARCHAR(30)   NOT NULL,  -- Housekeeping / Engineering / F&B / Front Desk / Guest Services
    Category        NVARCHAR(50)   NOT NULL,  -- Extra Linens, AC Issue, Noise Complaint, Room Service, etc.
    [Description]   NVARCHAR(500)  NULL,
    Priority        NVARCHAR(10)   NOT NULL,  -- Low / Medium / High / Urgent
    [Status]        NVARCHAR(20)   NOT NULL,  -- Open / InProgress / Completed / Escalated / Cancelled
    AssignedTo      NVARCHAR(100)  NULL,
    CompletedDate   DATETIME2      NULL,
    ResponseMinutes INT            NULL,      -- minutes from request to first action (AI signal)
    ResolutionNotes NVARCHAR(500)  NULL,
    GuestSatisfied  BIT            NULL       -- 1=satisfied, 0=not, NULL=unrated
);
"""

# ---------------------------------------------------------------------------
# System prompt template
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = f"""You are a SQL query generator for the Contoso Resorts hotel chain database.

Your job: convert a natural language question into a valid, read-only SQL SELECT statement.

## Rules
1. Output ONLY the SQL query — no explanation, no markdown, no code fences.
2. Generate only SELECT statements. Never generate INSERT, UPDATE, DELETE, DROP, or any DDL.
3. Always use the `dbo.` schema prefix (e.g., `dbo.Guests`, not just `Guests`).
4. Always bracket the `[FreeText]` column in dbo.Surveys (it is a reserved word).
5. Use SQL Server syntax (T-SQL) — compatible with both Fabric SQL Database and Azure SQL Database.
6. Keep queries readable: use aliases, indent JOIN clauses.
7. Use TOP N (e.g., TOP 100) if the result set could be large and the question doesn't imply ALL rows.
8. When joining, prefer explicit column names over SELECT *.
9. If the question references a guest by name, use LIKE or match on FirstName + LastName.
10. For date calculations, use GETDATE() for "today" and DATEADD() for offsets.

## Database Schema
{SCHEMA_DDL}

## Tips
- Guest loyalty tiers: Platinum > Gold > Silver > Member
- "Stay history" means dbo.Bookings (Status = 'Completed')
- "Upcoming reservations" means dbo.Bookings (Status = 'Upcoming')
- "Survey feedback / comments" means dbo.Surveys, especially [FreeText]
- "Available rooms" means dbo.Inventory where Available > 0
- "Experiences" (spa, dining, activities) are in dbo.Experiences
- Cross-property analysis often requires JOINing Bookings → Properties + Surveys
- "Service requests" (in-stay ops requests) are in dbo.ServiceRequests
- Bracket reserved words: [FreeText], [Description], [Status] in ServiceRequests
- Slow responses: ResponseMinutes > 60 on Priority='High' or Priority='Urgent' are significant
"""
